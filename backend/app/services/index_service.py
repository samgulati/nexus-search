from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from pathlib import Path

from ..models import Document, DocumentIn, SearchResult, SearchResponse, StatsResponse
from ..search import HybridIndex


class IndexService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.documents: dict[str, Document] = {}
        self.content_hashes: set[str] = set()
        self.index = HybridIndex()
        self.search_latencies: deque[float] = deque(maxlen=1000)
        self.searches = 0
        self.started_at = time.perf_counter()

    @staticmethod
    def _hash(text: str) -> str:
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def add_document(self, item: DocumentIn, rebuild: bool = True) -> tuple[Document, bool]:
        content_hash = self._hash(item.text)
        with self._lock:
            if content_hash in self.content_hashes:
                existing = next(d for d in self.documents.values() if d.content_hash == content_hash)
                return existing, False

            doc = Document(
                id=content_hash[:16],
                title=item.title.strip(),
                text=" ".join(item.text.split()),
                url=item.url,
                source=item.source,
                content_hash=content_hash,
            )
            self.documents[doc.id] = doc
            self.content_hashes.add(content_hash)
            if rebuild:
                self.rebuild()
            return doc, True

    def add_many(self, items: list[DocumentIn]) -> tuple[int, int]:
        added = 0
        skipped = 0
        with self._lock:
            for item in items:
                _doc, created = self.add_document(item, rebuild=False)
                if created:
                    added += 1
                else:
                    skipped += 1
            if added:
                self.rebuild()
        return added, skipped


    def restore_documents(self, docs: list[Document]) -> int:
        """Replace the in-memory corpus from durable storage and rebuild indexes once."""
        with self._lock:
            self.documents = {doc.id: doc for doc in docs}
            self.content_hashes = {doc.content_hash for doc in docs}
            self.rebuild()
        return len(docs)

    def rebuild(self) -> None:
        combined = {
            doc_id: f"{doc.title}\n{doc.text}"
            for doc_id, doc in self.documents.items()
        }
        self.index.rebuild(combined)

    def load_seed_file(self, path: Path) -> int:
        if not path.exists():
            return 0
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [DocumentIn(**item) for item in data]
        added, _ = self.add_many(items)
        return added

    @staticmethod
    def _snippet(text: str, query: str, max_chars: int = 260) -> str:
        lowered = text.lower()
        terms = [t for t in query.lower().split() if len(t) > 2]
        positions = [lowered.find(t) for t in terms if lowered.find(t) >= 0]
        start = max(0, (min(positions) if positions else 0) - 70)
        end = min(len(text), start + max_chars)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet += "…"
        return snippet

    def search(self, query: str, mode: str = "hybrid", top_k: int = 10) -> SearchResponse:
        t0 = time.perf_counter()
        with self._lock:
            ranked, bm25_map, semantic_map = self.index.search(query, mode=mode, top_k=top_k)
            results = []
            for doc_id, score in ranked:
                doc = self.documents[doc_id]
                results.append(SearchResult(
                    id=doc.id,
                    title=doc.title,
                    url=doc.url,
                    snippet=self._snippet(doc.text, query),
                    score=round(float(score), 6),
                    bm25_score=round(float(bm25_map.get(doc_id, 0.0)), 6),
                    semantic_score=round(float(semantic_map.get(doc_id, 0.0)), 6),
                    source=doc.source,
                ))
        took_ms = (time.perf_counter() - t0) * 1000
        self.searches += 1
        self.search_latencies.append(took_ms)
        return SearchResponse(
            query=query,
            mode=mode,
            took_ms=round(took_ms, 3),
            total=len(results),
            results=results,
        )

    def get_stats(self) -> StatsResponse:
        latencies = sorted(self.search_latencies)
        if latencies:
            p50 = latencies[int((len(latencies) - 1) * 0.50)]
            p95 = latencies[int((len(latencies) - 1) * 0.95)]
            avg = sum(latencies) / len(latencies)
        else:
            p50 = p95 = avg = 0.0
        return StatsResponse(
            documents=len(self.documents),
            vocabulary_terms=self.index.bm25.vocabulary_size,
            semantic_provider=self.index.semantic.provider_name,
            searches=self.searches,
            p50_search_ms=round(p50, 3),
            p95_search_ms=round(p95, 3),
            avg_search_ms=round(avg, 3),
            uptime_seconds=round(time.perf_counter() - self.started_at, 1),
        )


index_service = IndexService()
