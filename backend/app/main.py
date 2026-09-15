from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cluster import ClusterService, cluster_service
from .config import settings
from .persistence import document_store
from .queueing import index_queue
from .models import (
    AskRequest,
    AskResponse,
    BatchIndexResponse,
    CrawlRequest,
    CrawlResponse,
    Document,
    DocumentBatch,
    DocumentIn,
    QueuedIndexResponse,
    SearchResponse,
    StatsResponse,
)
from .services import answer_service, crawler, index_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed = Path(__file__).resolve().parent / "data" / "seed_documents.json"

    # Shards use PostgreSQL as durable source-of-truth when DATABASE_URL exists.
    # The search structures remain local/in-memory and are rebuilt after restore.
    if settings.service_role == "shard" and document_store.enabled:
        document_store.initialize()
        restored = document_store.load_shard(settings.shard_id)
        if restored:
            index_service.restore_documents(restored)
        elif seed.exists():
            data = json.loads(seed.read_text(encoding="utf-8"))
            items = [DocumentIn(**item) for item in data]
            selected = [
                item for item in items
                if ClusterService.seed_belongs_here(item, settings.shard_id, settings.shard_count)
            ]
            before = set(index_service.documents)
            index_service.add_many(selected)
            created = [doc for doc_id, doc in index_service.documents.items() if doc_id not in before]
            document_store.upsert_many(settings.shard_id, created)
    elif not index_service.documents and seed.exists():
        if settings.service_role == "coordinator":
            # Coordinator owns routing; shards own the actual indexes.
            pass
        elif settings.service_role == "shard":
            data = json.loads(seed.read_text(encoding="utf-8"))
            items = [DocumentIn(**item) for item in data]
            selected = [
                item for item in items
                if ClusterService.seed_belongs_here(item, settings.shard_id, settings.shard_count)
            ]
            index_service.add_many(selected)
        else:
            index_service.load_seed_file(seed)
    try:
        yield
    finally:
        await index_queue.close()


app = FastAPI(
    title="Nexus — Distributed AI Search",
    description="Distributed hybrid BM25 + semantic retrieval with grounded answer synthesis.",
    version="2.0.0",
    lifespan=lifespan,
)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "nexus-search",
        "role": settings.service_role,
        "shard_id": settings.shard_id if settings.service_role == "shard" else None,
        "documents": len(index_service.documents),
    }


@app.get("/api/stats", response_model=StatsResponse)
async def stats() -> StatsResponse:
    return await cluster_service.get_stats()


@app.get("/api/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=2, max_length=500),
    mode: Literal["hybrid", "lexical", "semantic"] = "hybrid",
    top_k: int = Query(default=10, ge=1, le=25),
) -> SearchResponse:
    return await cluster_service.search(q, mode=mode, top_k=top_k)


@app.post("/api/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    search_response = await cluster_service.search(request.query, mode="hybrid", top_k=request.top_k)
    return await answer_service.answer_from_search(request.query, search_response)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled until ADMIN_TOKEN is configured")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def require_cluster(x_cluster_token: str | None = Header(default=None)) -> None:
    if not settings.cluster_token:
        raise HTTPException(status_code=403, detail="Internal cluster endpoints are disabled")
    if not x_cluster_token or not hmac.compare_digest(x_cluster_token, settings.cluster_token):
        raise HTTPException(status_code=401, detail="Invalid cluster token")


@app.post("/api/index/document", response_model=Document, dependencies=[Depends(require_admin)])
async def add_document(item: DocumentIn) -> Document:
    return await cluster_service.add_document(item)


@app.post(
    "/api/index/async",
    response_model=QueuedIndexResponse,
    status_code=202,
    dependencies=[Depends(require_admin)],
)
async def queue_document(item: DocumentIn) -> QueuedIndexResponse:
    if not index_queue.enabled:
        raise HTTPException(status_code=503, detail="Asynchronous indexing queue is not configured")
    try:
        return await index_queue.enqueue_document(item)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Indexing queue unavailable") from exc


@app.post("/api/crawl", response_model=CrawlResponse, dependencies=[Depends(require_admin)])
async def crawl(request: CrawlRequest) -> CrawlResponse:
    return await crawler.crawl(request, index_many=cluster_service.add_many)


# Internal shard API. It is token-protected even when the service has a public domain.
@app.get("/internal/search", response_model=SearchResponse, dependencies=[Depends(require_cluster)])
def internal_search(
    q: str = Query(min_length=2, max_length=500),
    mode: Literal["hybrid", "lexical", "semantic"] = "hybrid",
    top_k: int = Query(default=20, ge=1, le=100),
) -> SearchResponse:
    response = index_service.search(q, mode=mode, top_k=top_k)
    for result in response.results:
        result.shard_id = str(settings.shard_id)
    return response


@app.get("/internal/stats", response_model=StatsResponse, dependencies=[Depends(require_cluster)])
def internal_stats() -> StatsResponse:
    base = index_service.get_stats()
    base.role = settings.service_role
    base.shards = 1
    base.healthy_shards = 1
    return base


@app.post("/internal/index/document", response_model=Document, dependencies=[Depends(require_cluster)])
def internal_add_document(item: DocumentIn) -> Document:
    doc, created = index_service.add_document(item)
    if created and settings.service_role == "shard" and document_store.enabled:
        document_store.upsert(settings.shard_id, doc)
    return doc


@app.post("/internal/index/batch", response_model=BatchIndexResponse, dependencies=[Depends(require_cluster)])
def internal_add_batch(batch: DocumentBatch) -> BatchIndexResponse:
    before = set(index_service.documents)
    added, skipped = index_service.add_many(batch.documents)
    if added and settings.service_role == "shard" and document_store.enabled:
        created = [doc for doc_id, doc in index_service.documents.items() if doc_id not in before]
        document_store.upsert_many(settings.shard_id, created)
    return BatchIndexResponse(added=added, skipped=skipped)


# Single-container production serving: only coordinator/standalone should serve the public UI.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists() and settings.service_role != "shard":
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = frontend_dist / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
