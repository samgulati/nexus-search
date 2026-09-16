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
    shard_id: str | None = None


class SearchPlan(BaseModel):
    requested_mode: Literal["auto", "hybrid", "lexical", "semantic"]
    selected_mode: Literal["hybrid", "lexical", "semantic"]
    tier: Literal["quality", "balanced", "survival", "manual"]
    query_profile: Literal["exact", "natural_language"]
    query_tokens: int
    reasons: list[str]
    healthy_shards: int
    total_shards: int
    unavailable_circuits: int = 0
    inflight: int
    capacity: int
    load_ratio: float
    observed_p95_ms: float
    latency_budget_ms: float
    generation_allowed: bool


class SearchResponse(BaseModel):
    query: str
    mode: Literal["hybrid", "lexical", "semantic"]
    took_ms: float
    total: int
    results: list[SearchResult]
    plan: SearchPlan | None = None


class AskRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class Citation(BaseModel):
    index: int
    title: str
    url: str | None
    document_id: str


class EvidenceSummary(BaseModel):
    decision: Literal["answer", "answer_with_caveat", "abstain"]
    confidence: float = Field(ge=0.0, le=1.0)
    coverage: float = Field(ge=0.0, le=1.0)
    authority: float = Field(ge=0.0, le=1.0)
    independent_sources: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class AskResponse(BaseModel):
    query: str
    answer: str
    citations: list[Citation]
    retrieval_ms: float
    generation_ms: float
    model: str
    grounded: bool
    evidence: EvidenceSummary | None = None
    plan: SearchPlan | None = None


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
    fetched: int = 0
    html_pages: int = 0
    content_pages: int = 0
    empty_pages: int = 0
    chunks_extracted: int = 0


class StatsResponse(BaseModel):
    documents: int
    vocabulary_terms: int
    semantic_provider: str
    searches: int
    p50_search_ms: float
    p95_search_ms: float
    avg_search_ms: float
    uptime_seconds: float
    role: str = "standalone"
    shards: int = 0
    healthy_shards: int = 0


class DocumentBatch(BaseModel):
    documents: list[DocumentIn] = Field(min_length=1, max_length=250)


class BatchIndexResponse(BaseModel):
    added: int
    skipped: int


class IndexEvent(BaseModel):
    event_id: str
    document: DocumentIn
    attempt: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: str | None = None


class QueuedIndexResponse(BaseModel):
    event_id: str
    status: Literal["queued"] = "queued"
    topic: str
