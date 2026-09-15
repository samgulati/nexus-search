from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=20)
    url: str | None = None
    source: str = "manual"


class Document(DocumentIn):
    id: str
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str


class SearchResult(BaseModel):
    id: str
    title: str
    url: str | None
    snippet: str
    score: float
    bm25_score: float = 0.0
    semantic_score: float = 0.0
    source: str


class SearchResponse(BaseModel):
    query: str
    mode: Literal["hybrid", "lexical", "semantic"]
    took_ms: float
    total: int
    results: list[SearchResult]


class AskRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class Citation(BaseModel):
    index: int
    title: str
    url: str | None
    document_id: str


class AskResponse(BaseModel):
    query: str
    answer: str
    citations: list[Citation]
    retrieval_ms: float
    generation_ms: float
    model: str
    grounded: bool


class CrawlRequest(BaseModel):
    seeds: list[HttpUrl] = Field(min_length=1, max_length=10)
    max_pages: int = Field(default=20, ge=1, le=100)
    max_depth: int = Field(default=1, ge=0, le=3)
    same_domain_only: bool = True


class CrawlResponse(BaseModel):
    crawled: int
    indexed: int
    skipped: int
    failed: int
    took_ms: float


class StatsResponse(BaseModel):
    documents: int
    vocabulary_terms: int
    semantic_provider: str
    searches: int
    p50_search_ms: float
    p95_search_ms: float
    avg_search_ms: float
    uptime_seconds: float
