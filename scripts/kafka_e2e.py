from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

BROKER = "127.0.0.1:19092"
CLUSTER_TOKEN = "ci-cluster-token"
SHARD_PORTS = (19100, 19101)
WORKER_METRICS_PORTS = (19200, 19201)


@dataclass
class ManagedProcess:
    process: subprocess.Popen
    name: str

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}")


def start_shard(shard_id: int, port: int) -> ManagedProcess:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": "backend",
            "SERVICE_ROLE": "shard",
            "SHARD_ID": str(shard_id),
            "SHARD_COUNT": "2",
            "CLUSTER_TOKEN": CLUSTER_TOKEN,
            "SEMANTIC_PROVIDER": "local",
            "DATABASE_URL": "",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            "backend",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    managed = ManagedProcess(process, f"shard-{shard_id}")
    wait_http(f"http://127.0.0.1:{port}/api/health")
    return managed


def shard_urls(valid: bool = True) -> str:
    if valid:
        return ",".join(
            f"{idx}=http://127.0.0.1:{port}" for idx, port in enumerate(SHARD_PORTS)
        )
    return "0=http://127.0.0.1:65530"


def start_worker(
    *,
    topic: str,
    dlq: str,
    group: str,
    valid_shards: bool,
    metrics_port: int,
    retry_max: int = 1,
) -> ManagedProcess:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": "backend",
            "KAFKA_BOOTSTRAP_SERVERS": BROKER,
            "KAFKA_INDEX_TOPIC": topic,
            "KAFKA_DLQ_TOPIC": dlq,
            "KAFKA_CONSUMER_GROUP": group,
            "KAFKA_CLIENT_ID": f"nexus-ci-{group}",
            "SHARD_URLS": shard_urls(valid_shards),
            "CLUSTER_TOKEN": CLUSTER_TOKEN,
            "SHARD_TIMEOUT_SECONDS": "1",
            "INDEX_RETRY_MAX": str(retry_max),
            "INDEX_RETRY_BASE_SECONDS": "0.1",
            "INDEX_RETRY_MAX_SECONDS": "0.2",
            "METRICS_PORT": str(metrics_port),
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(2)
    if process.poll() is not None:
        output = process.stdout.read() if process.stdout else ""
        raise RuntimeError(f"worker exited early:\n{output}")
    wait_http(f"http://127.0.0.1:{metrics_port}/metrics")
    return ManagedProcess(process, f"worker-{group}")


def content_event(title: str, text: str) -> tuple[str, bytes]:
    normalized = " ".join(text.lower().split())
    event_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    payload = {
        "event_id": event_id,
        "document": {
            "title": title,
            "text": text,
            "url": None,
            "source": "ci",
        },
        "attempt": 0,
        "last_error": None,
    }
    return event_id, json.dumps(payload).encode("utf-8")


async def send(topic: str, key: str, payload: bytes) -> None:
    producer = AIOKafkaProducer(
        bootstrap_servers=BROKER,
        client_id="nexus-ci-producer",
        acks="all",
        enable_idempotence=True,
    )
    await producer.start()
    try:
        await producer.send_and_wait(
            topic,
            key=key.encode("utf-8"),
            value=payload,
        )
    finally:
        await producer.stop()


def internal_headers() -> dict[str, str]:
    return {"X-Cluster-Token": CLUSTER_TOKEN}


def worker_metrics(port: int) -> str:
    response = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=5)
    response.raise_for_status()
    return response.text


def assert_metric(metrics: str, metric: str, label_fragment: str) -> None:
    matching = [
        line for line in metrics.splitlines()
        if line.startswith(metric) and label_fragment in line
    ]
    assert matching, f"missing metric {metric} with {label_fragment}"


def total_documents() -> int:
    total = 0
    for port in SHARD_PORTS:
        response = httpx.get(
            f"http://127.0.0.1:{port}/internal/stats",
            headers=internal_headers(),
            timeout=5,
        )
        response.raise_for_status()
        total += int(response.json()["documents"])
    return total


def find_document(query: str):
    for shard_id, port in enumerate(SHARD_PORTS):
        response = httpx.get(
            f"http://127.0.0.1:{port}/internal/search",
            params={"q": query, "mode": "hybrid", "top_k": 5},
            headers=internal_headers(),
            timeout=5,
        )
        response.raise_for_status()
        for result in response.json()["results"]:
            haystack = (result["title"] + " " + result["snippet"]).lower()
            if query.lower() in haystack:
                return shard_id, result
    return None


async def wait_for_document(query: str, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = find_document(query)
        if found:
            return found
        await asyncio.sleep(0.25)
    raise AssertionError(f"document containing {query!r} was never indexed")


async def wait_for_dlq(topic: str, event_id: str, timeout: float = 20.0) -> dict:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BROKER,
        group_id=f"nexus-ci-dlq-reader-{uuid.uuid4().hex}",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await consumer.start()
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            batch = await consumer.getmany(timeout_ms=500)
            for records in batch.values():
                for record in records:
                    payload = json.loads(record.value)
                    if payload.get("event_id") == event_id:
                        return payload
        raise AssertionError(f"event {event_id} did not reach DLQ {topic}")
    finally:
        await consumer.stop()


async def main() -> None:
    run_id = uuid.uuid4().hex[:8]
    shards: list[ManagedProcess] = []
    workers: list[ManagedProcess] = []
    try:
        shards = [
            start_shard(0, SHARD_PORTS[0]),
            start_shard(1, SHARD_PORTS[1]),
        ]
        baseline = total_documents()
        seed_path = Path("backend/app/data/seed_documents.json")
        expected_seed_docs = len(json.loads(seed_path.read_text(encoding="utf-8")))

        assert baseline == expected_seed_docs, (
            f"expected {expected_seed_docs} seed docs across two shards, got {baseline}"
        )

        topic = f"nexus-index-ci-{run_id}"
        dlq = f"nexus-index-ci-dlq-{run_id}"
        worker = start_worker(
            topic=topic,
            dlq=dlq,
            group=f"nexus-indexers-ci-{run_id}",
            valid_shards=True,
            metrics_port=WORKER_METRICS_PORTS[0],
        )
        workers.append(worker)

        marker = f"quantumindex{run_id}"
        text = (
            f"{marker} proves Nexus asynchronous Kafka indexing through an idempotent "
            "consumer into one rendezvous-hashed search shard."
        )
        event_id, payload = content_event(f"Nexus Kafka E2E {marker}", text)
        await send(topic, event_id, payload)
        shard_id, result = await wait_for_document(marker)
        assert result["id"] == event_id[:16]
        assert total_documents() == baseline + 1
        print(f"HAPPY_PATH event={event_id} shard={shard_id} id={result['id']}")

        await send(topic, event_id, payload)
        await asyncio.sleep(1.0)
        assert total_documents() == baseline + 1
        metrics = worker_metrics(WORKER_METRICS_PORTS[0])
        assert_metric(metrics, "nexus_index_events_total", 'outcome="indexed"')
        assert_metric(metrics, "nexus_kafka_consumer_lag", f'topic="{topic}"')
        assert "nexus_index_event_duration_seconds_count" in metrics
        print(
            f"IDEMPOTENT_REPLAY event={event_id} documents={baseline + 1} "
            "metrics=indexed,lag,processing"
        )

        worker.stop()
        workers.remove(worker)

        fail_topic = f"nexus-index-ci-fail-{run_id}"
        fail_dlq = f"nexus-index-ci-fail-dlq-{run_id}"
        fail_worker = start_worker(
            topic=fail_topic,
            dlq=fail_dlq,
            group=f"nexus-indexers-ci-fail-{run_id}",
            valid_shards=False,
            metrics_port=WORKER_METRICS_PORTS[1],
            retry_max=1,
        )
        workers.append(fail_worker)

        fail_marker = f"dlqmarker{run_id}"
        fail_id, fail_payload = content_event(
            f"Nexus DLQ E2E {fail_marker}",
            f"{fail_marker} forces an indexing failure to validate retry and dead letter handling.",
        )
        await send(fail_topic, fail_id, fail_payload)
        dlq_event = await wait_for_dlq(fail_dlq, fail_id)
        assert dlq_event["attempt"] == 2
        assert dlq_event["last_error"]
        failure_metrics = worker_metrics(WORKER_METRICS_PORTS[1])
        assert_metric(failure_metrics, "nexus_index_events_total", 'outcome="retry"')
        assert_metric(failure_metrics, "nexus_index_events_total", 'outcome="dlq"')
        assert_metric(failure_metrics, "nexus_kafka_consumer_lag", f'topic="{fail_topic}"')
        print(
            f"RETRY_DLQ event={fail_id} attempts={dlq_event['attempt']} "
            "metrics=retry,dlq,lag "
            f"error={dlq_event['last_error'][:120]}"
        )

        print("KAFKA_E2E_OBSERVABILITY_OK")
    finally:
        for worker in workers:
            worker.stop()
        for shard in shards:
            shard.stop()


if __name__ == "__main__":
    asyncio.run(main())
