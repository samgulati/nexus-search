from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from opentelemetry.trace import SpanKind, Status, StatusCode

from .adaptive import adaptive_search_controller
from .cluster import ClusterService, cluster_service
from .config import settings
from .observability import (
    APP_INFLIGHT,
    REQUEST_GATE_EVENTS,
    extract_http_context,
    observe_http,
    render_metrics,
    trace_id_hex,
    tracer,
)
from .persistence import document_store
from .resilience import CapacityGate
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
request_gate = CapacityGate(settings.max_inflight_requests)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def load_shedding_middleware(request: Request, call_next):
    path = request.url.path
    protected = (
        path == "/api/search"
        or path == "/api/ask"
        or path == "/api/crawl"
        or path.startswith("/api/index/")
    )
    if not protected:
        return await call_next(request)

    if not await request_gate.try_acquire():
        REQUEST_GATE_EVENTS.labels("rejected").inc()
        return Response(
            content='{"detail":"Server is at request capacity"}',
            status_code=503,
            media_type="application/json",
            headers={"Retry-After": "1"},
        )

    REQUEST_GATE_EVENTS.labels("admitted").inc()
    APP_INFLIGHT.inc()
    try:
        return await call_next(request)
    finally:
        APP_INFLIGHT.dec()
        await request_gate.release()


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    import time

    started = time.perf_counter()
    path = request.url.path
    status_code = 500
    parent = extract_http_context(request.headers)

    with tracer().start_as_current_span(
        f"{request.method} {path}",
        context=parent,
        kind=SpanKind.SERVER,
    ) as span:
        span.set_attribute("http.request.method", request.method)
        span.set_attribute("url.path", path)
        span.set_attribute("nexus.role", settings.service_role)
        if settings.service_role == "shard":
            span.set_attribute("nexus.shard_id", settings.shard_id)

        try:
            response = await call_next(request)
            status_code = response.status_code
            span.set_attribute("http.response.status_code", status_code)
            if status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))

            current_trace_id = trace_id_hex()
            if current_trace_id:
                response.headers["X-Trace-ID"] = current_trace_id
            return response
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            observe_http(
                request.method,
                path,
                status_code,
                time.perf_counter() - started,
            )


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled")
    payload, content_type = render_metrics()
    return Response(content=payload, headers={"Content-Type": content_type})


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "nexus-search",
        "role": settings.service_role,
        "shard_id": settings.shard_id if settings.service_role == "shard" else None,
        "documents": len(index_service.documents),
    }


@app.get("/api/ready")
async def ready() -> Response:
    if settings.service_role != "coordinator":
        return Response(
            content=json.dumps({"status": "ready", "role": settings.service_role}),
            media_type="application/json",
        )

    stats = await cluster_service.get_stats()
    minimum = max(1, min(settings.min_ready_shards, max(1, stats.shards)))
    is_ready = stats.healthy_shards >= minimum
    payload = {
        "status": "ready" if is_ready else "not_ready",
        "role": "coordinator",
        "healthy_shards": stats.healthy_shards,
        "required_shards": minimum,
        "total_shards": stats.shards,
    }
    return Response(
        content=json.dumps(payload),
        status_code=200 if is_ready else 503,
        media_type="application/json",
    )


@app.get("/api/stats", response_model=StatsResponse)
async def stats() -> StatsResponse:
    return await cluster_service.get_stats()


@app.get("/api/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=2, max_length=500),
    mode: Literal["auto", "hybrid", "lexical", "semantic"] = "auto",
    top_k: int = Query(default=10, ge=1, le=25),
) -> SearchResponse:
    inflight = await request_gate.inflight()
    plan = await adaptive_search_controller.plan(
        query=q,
        requested_mode=mode,
        inflight=inflight,
        capacity=request_gate.capacity,
        cluster_service=cluster_service,
    )
    response = await cluster_service.search(q, mode=plan.selected_mode, top_k=top_k)
    plan = plan.model_copy(update={"healthy_shards": cluster_service.last_healthy_shards})
    return response.model_copy(update={"plan": plan})


@app.post("/api/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    inflight = await request_gate.inflight()
    plan = await adaptive_search_controller.plan(
        query=request.query,
        requested_mode="auto",
        inflight=inflight,
        capacity=request_gate.capacity,
        cluster_service=cluster_service,
    )
    search_response = await cluster_service.search(
        request.query,
        mode=plan.selected_mode,
        top_k=request.top_k,
    )
    plan = plan.model_copy(update={"healthy_shards": cluster_service.last_healthy_shards})
    search_response = search_response.model_copy(update={"plan": plan})
    return await answer_service.answer_from_search(
        request.query,
        search_response,
        force_extractive=not plan.generation_allowed,
    )


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
