from __future__ import annotations

from collections import Counter

import httpx
import numpy as np

from ..config import settings
from .tokenize import tokenize


class LSAIndex:
    """Latent-semantic vector index implemented from scratch with NumPy SVD."""

    provider_name = "local LSA vectors (TF-IDF + SVD)"

    def __init__(self, dimensions: int = 48, max_features: int = 1200) -> None:
        self.dimensions = dimensions
        self.max_features = max_features
        self.doc_ids: list[str] = []
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray = np.array([], dtype=np.float32)
        self.components: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self.doc_vectors: np.ndarray = np.empty((0, 0), dtype=np.float32)

    def rebuild(self, docs: dict[str, str]) -> None:
        self.doc_ids = list(docs.keys())
        n_docs = len(self.doc_ids)
        if not n_docs:
            self.vocab = {}
            self.idf = np.array([], dtype=np.float32)
            self.components = np.empty((0, 0), dtype=np.float32)
            self.doc_vectors = np.empty((0, 0), dtype=np.float32)
            return

        tokenized = [tokenize(docs[doc_id]) for doc_id in self.doc_ids]
        document_frequency: Counter[str] = Counter()
        for tokens in tokenized:
            document_frequency.update(set(tokens))

        terms = [t for t, _ in document_frequency.most_common(self.max_features)]
        self.vocab = {term: idx for idx, term in enumerate(terms)}
        if not terms:
            return

        matrix = np.zeros((n_docs, len(terms)), dtype=np.float32)
        for row, tokens in enumerate(tokenized):
            counts = Counter(tokens)
            length = max(len(tokens), 1)
            for term, count in counts.items():
                col = self.vocab.get(term)
                if col is not None:
                    matrix[row, col] = count / length

        df = np.array([document_frequency[t] for t in terms], dtype=np.float32)
        self.idf = np.log((1 + n_docs) / (1 + df)) + 1.0
        matrix *= self.idf

        k = min(self.dimensions, max(1, min(matrix.shape) - 1))
        try:
            u, s, vt = np.linalg.svd(matrix, full_matrices=False)
            self.components = vt[:k]
            self.doc_vectors = u[:, :k] * s[:k]
            self.doc_vectors = self._normalize_rows(self.doc_vectors)
        except np.linalg.LinAlgError:
            self.components = np.eye(min(matrix.shape), matrix.shape[1], dtype=np.float32)[:k]
            self.doc_vectors = self._normalize_rows(matrix @ self.components.T)

    @staticmethod
    def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
        if matrix.size == 0:
            return matrix
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def _query_vector(self, query: str) -> np.ndarray | None:
        if not self.vocab or self.components.size == 0:
            return None
        tokens = tokenize(query)
        if not tokens:
            return None
        counts = Counter(tokens)
        q = np.zeros(len(self.vocab), dtype=np.float32)
        length = max(len(tokens), 1)
        for term, count in counts.items():
            col = self.vocab.get(term)
            if col is not None:
                q[col] = (count / length) * self.idf[col]
        latent = q @ self.components.T
        norm = np.linalg.norm(latent)
        if norm == 0:
            return None
        return latent / norm

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        q = self._query_vector(query)
        if q is None or self.doc_vectors.size == 0:
            return []
        scores = self.doc_vectors @ q
        order = np.argsort(scores)[::-1][:top_k]
        return [(self.doc_ids[int(i)], float(scores[int(i)])) for i in order if scores[int(i)] > 0]


class SemanticIndex:
    """Neural embeddings when configured, with a fully local LSA fallback.

    Both indexes are built so a provider outage does not make search unavailable.
    """

    def __init__(self) -> None:
        self.lsa = LSAIndex()
        self.doc_ids: list[str] = []
        self.neural_vectors: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self._provider = self.lsa.provider_name

    @property
    def provider_name(self) -> str:
        return self._provider

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        if matrix.size == 0:
            return matrix
        if matrix.ndim == 1:
            norm = np.linalg.norm(matrix)
            return matrix if norm == 0 else matrix / norm
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def _embedding_request(self, inputs: list[str]) -> np.ndarray:
        response = httpx.post(
            f"{settings.openai_base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={"model": settings.embedding_model, "input": inputs},
            timeout=45,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        vectors = np.array([row["embedding"] for row in sorted(data, key=lambda x: x["index"])], dtype=np.float32)
        return self._normalize(vectors)

    def rebuild(self, docs: dict[str, str]) -> None:
        self.lsa.rebuild(docs)
        self.doc_ids = list(docs.keys())
        self.neural_vectors = np.empty((0, 0), dtype=np.float32)
        self._provider = self.lsa.provider_name

        wants_neural = settings.semantic_provider in {"auto", "openai"} and bool(settings.openai_api_key)
        if not wants_neural or not docs:
            return
        try:
            texts = [docs[doc_id][:8000] for doc_id in self.doc_ids]
            self.neural_vectors = self._embedding_request(texts)
            self._provider = f"OpenAI {settings.embedding_model} (LSA fallback)"
        except Exception:
            self.neural_vectors = np.empty((0, 0), dtype=np.float32)
            self._provider = self.lsa.provider_name

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if self.neural_vectors.size:
            try:
                q = self._embedding_request([query])[0]
                scores = self.neural_vectors @ q
                order = np.argsort(scores)[::-1][:top_k]
                return [(self.doc_ids[int(i)], float(scores[int(i)])) for i in order]
            except Exception:
                pass
        return self.lsa.search(query, top_k=top_k)
