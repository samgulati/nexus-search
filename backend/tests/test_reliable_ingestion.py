from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from app.cluster import ClusterService, ShardTarget
from app.models import DocumentIn


def make_docs(count: int) -> list[DocumentIn]:
    return [
        DocumentIn(
            title=f"Document {i}",
            text=f"Reliable distributed ingestion document number {i} with enough body text for testing.",
            url=f"https://example.com/docs/{i}",
            source="test",
        )
        for i in range(count)
    ]


def test_failed_batch_accounts_for_documents_not_one_batch(monkeypatch):
    service = ClusterService()
    target = ShardTarget("0", "http://shard-0")

    class AlwaysFailClient:
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("boom")

    from app import cluster as cluster_module
    monkeypatch.setattr(
        cluster_module,
        "settings",
        replace(cluster_module.settings, ingest_batch_size=4, ingest_batch_retries=0),
    )

    added, skipped = asyncio.run(
        service._index_target_batches(AlwaysFailClient(), target, make_docs(7))
    )
    assert added == 0
    assert skipped == 7


def test_micro_batches_are_bounded_and_complete(monkeypatch):
    service = ClusterService()
    target = ShardTarget("0", "http://shard-0")
    sizes: list[int] = []

    class FakeResponse:
        def __init__(self, size: int):
            self.size = size
        def raise_for_status(self):
            return None
        def json(self):
            return {"added": self.size, "skipped": 0}

    class RecordingClient:
        async def post(self, *args, **kwargs):
            size = len(kwargs["json"]["documents"])
            sizes.append(size)
            return FakeResponse(size)

    from app import cluster as cluster_module
    monkeypatch.setattr(
        cluster_module,
        "settings",
        replace(cluster_module.settings, ingest_batch_size=3, ingest_batch_retries=0),
    )

    added, skipped = asyncio.run(
        service._index_target_batches(RecordingClient(), target, make_docs(8))
    )
    assert sizes == [3, 3, 2]
    assert added == 8
    assert skipped == 0
