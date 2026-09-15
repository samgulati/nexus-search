from __future__ import annotations

from .bm25 import BM25Index
from .semantic import SemanticIndex


class HybridIndex:
    def __init__(self) -> None:
        self.bm25 = BM25Index()
        self.semantic = SemanticIndex()

    def rebuild(self, docs: dict[str, str]) -> None:
        self.bm25.rebuild(docs)
        self.semantic.rebuild(docs)

    @staticmethod
    def _rrf(ranked_lists: list[list[tuple[str, float]]], k: int = 60) -> list[tuple[str, float]]:
        scores: dict[str, float] = {}
        for ranked in ranked_lists:
            for rank, (doc_id, _raw_score) in enumerate(ranked, start=1):
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def search(self, query: str, mode: str = "hybrid", top_k: int = 10) -> tuple[list[tuple[str, float]], dict[str, float], dict[str, float]]:
        bm25 = self.bm25.search(query, top_k=max(top_k * 3, 20))
        semantic = self.semantic.search(query, top_k=max(top_k * 3, 20))
        bm25_map = dict(bm25)
        semantic_map = dict(semantic)

        if mode == "lexical":
            ranked = bm25[:top_k]
        elif mode == "semantic":
            ranked = semantic[:top_k]
        else:
            ranked = self._rrf([bm25, semantic])[:top_k]
        return ranked, bm25_map, semantic_map
