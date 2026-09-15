from __future__ import annotations

import asyncio
import hashlib

from aiokafka import AIOKafkaProducer

from .config import settings
from .models import DocumentIn, IndexEvent, QueuedIndexResponse


def event_id_for_document(item: DocumentIn) -> str:
    """Create a stable idempotency key from normalized document content."""
    normalized = " ".join(item.text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class KafkaIndexQueue:
    """Lazy Kafka producer used by the coordinator's async indexing endpoint."""

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(settings.kafka_bootstrap_servers.strip())

    async def _get_producer(self) -> AIOKafkaProducer:
        if self._producer is not None:
            return self._producer
        if not self.enabled:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is not configured")

        async with self._lock:
            if self._producer is not None:
                return self._producer
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                client_id=settings.kafka_client_id,
                acks="all",
                enable_idempotence=True,
            )
            try:
                await producer.start()
            except Exception:
                try:
                    await producer.stop()
                except Exception:
                    pass
                raise
            self._producer = producer
            return producer

    async def enqueue_document(self, item: DocumentIn) -> QueuedIndexResponse:
        producer = await self._get_producer()
        event = IndexEvent(
            event_id=event_id_for_document(item),
            document=item,
        )
        await producer.send_and_wait(
            settings.kafka_index_topic,
            value=event.model_dump_json().encode("utf-8"),
            key=event.event_id.encode("utf-8"),
        )
        return QueuedIndexResponse(
            event_id=event.event_id,
            topic=settings.kafka_index_topic,
        )

    async def close(self) -> None:
        if self._producer is None:
            return
        producer, self._producer = self._producer, None
        await producer.stop()


index_queue = KafkaIndexQueue()
