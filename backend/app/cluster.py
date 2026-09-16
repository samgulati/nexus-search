from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

import httpx

from .config import settings
from .models import Document, DocumentIn, SearchResponse, SearchResult, StatsResponse
from .observability import (
    CIRCUIT_EVENTS,
    SHARD_INFLIGHT,
    inject_trace_headers,
    observe_shard_call,
    tracer,
)
from .resilience import CircuitBreaker
from .services.index_service import index_service
from .search.tokenize import tokenize


class RendezvousHash:
    """Deterministic highest-random-weight (rendezvous) hashing.

    It gives every key a stable shard assignment without maintaining a ring, and
    when the shard set changes only keys whose winning node changes are remapped.
    """

    def __init__(self, nodes: Iterable[str]) -> None:
        self.nodes = tuple(str(n) for n in nodes)
        if not self.nodes:
            raise ValueError("RendezvousHash requires at least one node")

    @staticmethod
    def _score(key: str, node: str) -> int:
        digest = hashlib.sha256(f"{key}|{node}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big", signed=False)

    def pick(self, key: str) -> str:
        return max(self.nodes, key=lambda node: self._score(key, node))


@dataclass(frozen=True)
class ShardTarget:
    shard_id: str
    url: str


def _parse_shards(raw: str) -> list[ShardTarget]:
    """Parse `SHARD_URLS` as `id=url,id=url` or plain comma-separated URLs."""
    targets: list[ShardTarget] = []
    for idx, part in enumerate(p.strip() for p in raw.split(",") if p.strip()):
        if "=" in part:
            shard_id, url = part.split("=", 1)
            targets.append(ShardTarget(shard_id.strip(), url.rstrip("/")))
        else:
            targets.append(ShardTarget(str(idx), part.rstrip("/")))
    return targets


class ClusterService:
    def __init__(self) -> None:
        self.role = settings.service_role
        self.shard_targets = _parse_shards(settings.shard_urls)
        self.search_latencies: deque[float] = deque(maxlen=1000)
        self.searches = 0
        self.started_at = time.perf_counter()
        self.last_healthy_shards = len(self.shard_targets) if self.shard_targets else 1
        self._shard_semaphore = asyncio.Semaphore(max(1, settings.shard_max_concurrency))
        self._circuits = {
            target.shard_id: CircuitBreaker(
                settings.circuit_failure_threshold,
                settings.circuit_recovery_seconds,
            )
            for target in self.shard_targets
        }

    @property
    def is_coordinator(self) -> bool:
        return self.role == "coordinator"

    @property
    def is_shard(self) -> bool:
        return self.role == "shard"

    @staticmethod
    def key_for_document(item: DocumentIn) -> str:
        if item.url:
            return item.url
        normalized = " ".join(item.text.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def seed_belongs_here(item: DocumentIn, shard_id: str, shard_count: int) -> bool:
        if shard_count <= 1:
            return True
        ring = RendezvousHash(str(i) for i in range(shard_count))
        return ring.pick(ClusterService.key_for_document(item)) == str(shard_id)

    def _cluster_headers(self) -> dict[str, str]:
        headers = {"X-Cluster-Token": settings.cluster_token} if settings.cluster_token else {}
        return inject_trace_headers(headers)

    async def search(self, query: str, mode: str = "hybrid", top_k: int = 10) -> SearchResponse:
        if not self.is_coordinator:
            response = index_service.search(query, mode=mode, top_k=top_k)
            shard_id = settings.shard_id if self.is_shard else None
            if shard_id is not None:
                for result in response.results:
                    result.shard_id = str(shard_id)
            return response

        t0 = time.perf_counter()
        if not self.shard_targets:
            # Fail safe during initial provisioning: coordinator remains usable.
            return index_service.search(query, mode=mode, top_k=top_k)

        async with httpx.AsyncClient(timeout=settings.shard_timeout_seconds) as client:
            tasks = [
                self._search_one(client, target, query, mode, max(top_k * 3, 20))
                for target in self.shard_targets
            ]
            shard_responses = await asyncio.gather(*tasks)

        available = [response for response in shard_responses if response is not None]
        self.last_healthy_shards = len(available)
        merged = self.merge_ranked(available, top_k=top_k, query=query)
        took_ms = (time.perf_counter() - t0) * 1000
        self.searches += 1
        self.search_latencies.append(took_ms)
        return SearchResponse(
            query=query,
            mode=mode,
            took_ms=round(took_ms, 3),
            total=len(merged),
            results=merged,
        )

    async def _search_one(
        self,
        client: httpx.AsyncClient,
        target: ShardTarget,
        query: str,
        mode: str,
        top_k: int,
    ) -> SearchResponse | None:
        started = time.perf_counter()
        circuit = self._circuits[target.shard_id]

        if not await circuit.allow_request():
            CIRCUIT_EVENTS.labels(target.shard_id, "rejected").inc()
            observe_shard_call("search", target.shard_id, "circuit_open", time.perf_counter() - started)
            return None

        async with self._shard_semaphore:
            SHARD_INFLIGHT.inc()
            try:
                with tracer().start_as_current_span("nexus.shard.search") as span:
                    span.set_attribute("nexus.shard_id", target.shard_id)
                    try:
                        response = await client.get(
                            f"{target.url}/internal/search",
                            params={"q": query, "mode": mode, "top_k": top_k},
                            headers=self._cluster_headers(),
                        )
                        response.raise_for_status()
                        parsed = SearchResponse.model_validate(response.json())
                        for result in parsed.results:
                            result.shard_id = target.shard_id
                        await circuit.record_success()
                        CIRCUIT_EVENTS.labels(target.shard_id, "success").inc()
                        observe_shard_call("search", target.shard_id, "success", time.perf_counter() - started)
                        return parsed
                    except Exception as exc:
                        span.record_exception(exc)
                        await circuit.record_failure()
                        CIRCUIT_EVENTS.labels(target.shard_id, "failure").inc()
                        observe_shard_call("search", target.shard_id, "error", time.perf_counter() - started)
                        return None
            finally:
                SHARD_INFLIGHT.dec()

    @staticmethod
    def _title_overlap(query: str, title: str) -> float:
        """Return query-token coverage in a title for deterministic RRF tie-breaking.

        Cross-shard BM25/LSA raw scores are not guaranteed to be calibrated to the
        same scale, so the coordinator keeps shard rank as the primary signal.
        Title overlap is used only when two candidates have the same global RRF
        score (a common case for rank-1 results from different shards).
        """
        query_terms = set(tokenize(query))
        if not query_terms:
            return 0.0
        title_terms = set(tokenize(title))
        return len(query_terms & title_terms) / len(query_terms)

    @staticmethod
    def merge_ranked(
        responses: list[SearchResponse],
        top_k: int,
        query: str = "",
        rrf_k: int = 60,
    ) -> list[SearchResult]:
        """Merge independently-ranked shard results with rank-first semantics.

        RRF remains the primary cross-shard signal because shard-local BM25 and
        local-LSA scores are not globally calibrated. Equal RRF scores are broken
        deterministically by query/title coverage, then local lexical/semantic
        scores. This prevents the first shard in the fan-out list from winning
        every rank tie while avoiding raw-score comparison as the primary merge.
        """
        fused: dict[str, float] = {}
        by_id: dict[str, SearchResult] = {}
        for response in responses:
            for rank, result in enumerate(response.results, start=1):
                fused[result.id] = fused.get(result.id, 0.0) + 1.0 / (rrf_k + rank)
                by_id[result.id] = result

        def sort_key(item: tuple[str, float]) -> tuple[float, float, float, float, str]:
            doc_id, rrf_score = item
            result = by_id[doc_id]
            return (
                rrf_score,
                ClusterService._title_overlap(query, result.title),
                result.bm25_score,
                result.semantic_score,
                doc_id,
            )

        ordered = sorted(fused.items(), key=sort_key, reverse=True)[:top_k]
        merged: list[SearchResult] = []
        for doc_id, score in ordered:
            original = by_id[doc_id]
            merged.append(original.model_copy(update={"score": round(score, 6)}))
        return merged

    def observed_p95_ms(self) -> float:
        latencies = sorted(self.search_latencies)
        if not latencies:
            return 0.0
        return float(latencies[int((len(latencies) - 1) * 0.95)])

    async def circuit_state_counts(self) -> dict[str, int]:
        counts = {"closed": 0, "open": 0, "half_open": 0}
        for circuit in self._circuits.values():
            snapshot = await circuit.snapshot()
            counts[snapshot.state] = counts.get(snapshot.state, 0) + 1
        return counts

    async def add_document(self, item: DocumentIn) -> Document:
        if not self.is_coordinator or not self.shard_targets:
            doc, _created = index_service.add_document(item)
            return doc

        ring = RendezvousHash(t.shard_id for t in self.shard_targets)
        shard_id = ring.pick(self.key_for_document(item))
        target = next(t for t in self.shard_targets if t.shard_id == shard_id)
        async with httpx.AsyncClient(timeout=settings.shard_timeout_seconds) as client:
            response = await client.post(
                f"{target.url}/internal/index/document",
                json=item.model_dump(mode="json"),
                headers=self._cluster_headers(),
            )
            response.raise_for_status()
            return Document.model_validate(response.json())

    async def _index_target_batches(
        self,
        client: httpx.AsyncClient,
        target: ShardTarget,
        items: list[DocumentIn],
    ) -> tuple[int, int]:
        added = 0
        skipped = 0
        batch_size = settings.ingest_batch_size

        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            payload = {"documents": [item.model_dump(mode="json") for item in batch]}
            last_error: Exception | None = None

            for attempt in range(settings.ingest_batch_retries + 1):
                try:
                    response = await client.post(
                        f"{target.url}/internal/index/batch",
                        json=payload,
                        headers=self._cluster_headers(),
                    )
                    response.raise_for_status()
                    body = response.json()
                    added += int(body.get("added", 0))
                    skipped += int(body.get("skipped", 0))
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < settings.ingest_batch_retries:
                        await asyncio.sleep(min(0.25 * (2 ** attempt), 1.0))

            if last_error is not None:
                skipped += len(batch)

        return added, skipped

    async def add_many(self, items: list[DocumentIn]) -> tuple[int, int]:
        if not items:
            return 0, 0
        if not self.is_coordinator or not self.shard_targets:
            return index_service.add_many(items)

        ring = RendezvousHash(t.shard_id for t in self.shard_targets)
        groups: dict[str, list[DocumentIn]] = {t.shard_id: [] for t in self.shard_targets}
        for item in items:
            groups[ring.pick(self.key_for_document(item))].append(item)

        timeout = httpx.Timeout(settings.ingest_batch_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks = [
                self._index_target_batches(client, target, groups[target.shard_id])
                for target in self.shard_targets
                if groups[target.shard_id]
            ]
            results = await asyncio.gather(*tasks)

        return (
            sum(result[0] for result in results),
            sum(result[1] for result in results),
        )

    async def get_stats(self) -> StatsResponse:
        if not self.is_coordinator or not self.shard_targets:
            base = index_service.get_stats()
            base.role = self.role
            base.shards = 1 if self.is_shard else 0
            base.healthy_shards = 1 if self.is_shard else 0
            return base

        async with httpx.AsyncClient(timeout=settings.shard_timeout_seconds) as client:
            tasks = [
                client.get(f"{target.url}/internal/stats", headers=self._cluster_headers())
                for target in self.shard_targets
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

        stats: list[StatsResponse] = []
        for response in responses:
            if isinstance(response, Exception):
                continue
            try:
                response.raise_for_status()
                stats.append(StatsResponse.model_validate(response.json()))
            except Exception:
                continue

        self.last_healthy_shards = len(stats)

        latencies = sorted(self.search_latencies)
        if latencies:
            p50 = latencies[int((len(latencies) - 1) * 0.50)]
            p95 = latencies[int((len(latencies) - 1) * 0.95)]
            avg = sum(latencies) / len(latencies)
        else:
            p50 = p95 = avg = 0.0

        return StatsResponse(
            documents=sum(s.documents for s in stats),
            vocabulary_terms=sum(s.vocabulary_terms for s in stats),
            semantic_provider="distributed hybrid retrieval",
            searches=self.searches,
            p50_search_ms=round(p50, 3),
            p95_search_ms=round(p95, 3),
            avg_search_ms=round(avg, 3),
            uptime_seconds=round(time.perf_counter() - self.started_at, 1),
            role="coordinator",
            shards=len(self.shard_targets),
            healthy_shards=len(stats),
        )


cluster_service = ClusterService()
