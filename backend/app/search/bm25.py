from __future__ import annotations

import math
from collections import Counter, defaultdict

from .tokenize import tokenize


class BM25Index:
    """A small, dependency-free BM25 implementation with an inverted index."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.doc_lengths: dict[str, int] = {}
        self.doc_terms: dict[str, Counter[str]] = {}
        self.postings: dict[str, dict[str, int]] = defaultdict(dict)
        self.avg_doc_len = 0.0

    def rebuild(self, docs: dict[str, str]) -> None:
        self.doc_lengths.clear()
        self.doc_terms.clear()
        self.postings = defaultdict(dict)

        for doc_id, text in docs.items():
            tokens = tokenize(text)
            counts = Counter(tokens)
            self.doc_lengths[doc_id] = len(tokens)
            self.doc_terms[doc_id] = counts
            for term, tf in counts.items():
                self.postings[term][doc_id] = tf

        n = len(self.doc_lengths)
        self.avg_doc_len = (sum(self.doc_lengths.values()) / n) if n else 0.0

    @property
    def vocabulary_size(self) -> int:
        return len(self.postings)

    def _idf(self, term: str) -> float:
        n = len(self.doc_lengths)
        df = len(self.postings.get(term, {}))
        if not n or not df:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if not self.doc_lengths:
            return []

        scores: dict[str, float] = defaultdict(float)
        for term in tokenize(query):
            idf = self._idf(term)
            for doc_id, tf in self.postings.get(term, {}).items():
                dl = self.doc_lengths[doc_id]
                norm = self.k1 * (1 - self.b + self.b * dl / max(self.avg_doc_len, 1e-9))
                scores[doc_id] += idf * (tf * (self.k1 + 1)) / (tf + norm)

        return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
