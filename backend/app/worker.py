from __future__ import annotations

import asyncio
import logging

import httpx
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .cluster import ClusterService, RendezvousHash, ShardTarget
from .config import settings
from .models import IndexEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("nexus.indexer")


def parse_shards(raw: str) -> list[ShardTarget]:
    targets: list[ShardTarget] = []
    for idx, part in enumerate(p.strip() for p in raw.split(",") if p.strip()):
        if "=" in part:
            shard_id, url = part.split("=", 1)
            targets.append(ShardTarget(shard_id.strip(), url.rstrip("/")))
        else:
            targets.append(ShardTarget(str(idx), part.rstrip("/")))
    return targets


def retry_delay(attempt: int) -> float:
    exponent = min(max(0, attempt - 1), 30)
    raw = settings.index_retry_base_seconds * (2 ** exponent)
    return min(raw, settings.index_retry_max_seconds)


async def publish_event(
    producer: AIOKafkaProducer,
    topic: str,
    event: IndexEvent,
) -> None:
    await producer.send_and_wait(
        topic,
        key=event.event_id.encode("utf-8"),
        value=event.model_dump_json().encode("utf-8"),
    )


async def route_to_shard(
    client: httpx.AsyncClient,
    event: IndexEvent,
    targets: list[ShardTarget],
) -> str:
    ring = RendezvousHash(t.shard_id for t in targets)
    shard_id = ring.pick(ClusterService.key_for_document(event.document))
    target = next(t for t in targets if t.shard_id == shard_id)
    headers = {"X-Cluster-Token": settings.cluster_token}

    response = await client.post(
        f"{target.url}/internal/index/document",
        json=event.document.model_dump(mode="json"),
        headers=headers,
    )
    response.raise_for_status()
    return shard_id


async def run() -> None:
    if not settings.kafka_bootstrap_servers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required for the index worker")
    if not settings.shard_urls:
        raise RuntimeError("SHARD_URLS is required for the index worker")
    if not settings.cluster_token:
        raise RuntimeError("CLUSTER_TOKEN is required for the index worker")

    targets = parse_shards(settings.shard_urls)
    if not targets:
        raise RuntimeError("No shard targets configured")

    consumer = AIOKafkaConsumer(
        settings.kafka_index_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group,
        client_id=f"{settings.kafka_client_id}-worker",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        client_id=f"{settings.kafka_client_id}-retry",
        acks="all",
        enable_idempotence=True,
    )

    await consumer.start()
    await producer.start()
    logger.info(
        "index worker started topic=%s group=%s shards=%s",
        settings.kafka_index_topic,
        settings.kafka_consumer_group,
        [t.shard_id for t in targets],
    )

    try:
        async with httpx.AsyncClient(timeout=max(settings.shard_timeout_seconds, 10.0)) as client:
            async for message in consumer:
                try:
                    event = IndexEvent.model_validate_json(message.value)
                except Exception:
                    logger.exception(
                        "invalid index event partition=%s offset=%s; sending raw payload to DLQ",
                        message.partition,
                        message.offset,
                    )
                    await producer.send_and_wait(
                        settings.kafka_dlq_topic,
                        key=message.key,
                        value=message.value,
                    )
                    await consumer.commit()
                    continue

                try:
                    shard_id = await route_to_shard(client, event, targets)
                    await consumer.commit()
                    logger.info(
                        "indexed event=%s shard=%s attempt=%s",
                        event.event_id,
                        shard_id,
                        event.attempt,
                    )
                except Exception as exc:
                    next_attempt = event.attempt + 1
                    updated = event.model_copy(
                        update={
                            "attempt": next_attempt,
                            "last_error": str(exc)[:500],
                        }
                    )
                    if next_attempt <= settings.index_retry_max:
                        delay = retry_delay(next_attempt)
                        logger.warning(
                            "indexing failed event=%s attempt=%s retry_in=%.2fs",
                            event.event_id,
                            next_attempt,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        await publish_event(producer, settings.kafka_index_topic, updated)
                    else:
                        logger.error(
                            "indexing exhausted retries event=%s; sending to DLQ",
                            event.event_id,
                        )
                        await publish_event(producer, settings.kafka_dlq_topic, updated)
                    # Commit only after the retry/DLQ write is acknowledged.
                    await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(run())
