from __future__ import annotations

import hmac
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import (
    AskRequest,
    AskResponse,
    CrawlRequest,
    CrawlResponse,
    Document,
    DocumentIn,
    SearchResponse,
    StatsResponse,
)
from .services import answer_service, crawler, index_service

@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed = Path(__file__).resolve().parent / "data" / "seed_documents.json"
    if not index_service.documents:
        index_service.load_seed_file(seed)
    yield


app = FastAPI(
    title="Nexus — Distributed AI Search",
    description="Hybrid BM25 + latent-semantic retrieval with grounded AI synthesis.",
    version="1.0.0",
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
    return {"status": "ok", "service": "nexus-search", "documents": len(index_service.documents)}


@app.get("/api/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    return index_service.get_stats()


@app.get("/api/search", response_model=SearchResponse)
def search(
    q: str = Query(min_length=2, max_length=500),
    mode: Literal["hybrid", "lexical", "semantic"] = "hybrid",
    top_k: int = Query(default=10, ge=1, le=25),
) -> SearchResponse:
    return index_service.search(q, mode=mode, top_k=top_k)


@app.post("/api/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    return await answer_service.answer(request.query, request.top_k)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled until ADMIN_TOKEN is configured")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")


@app.post("/api/index/document", response_model=Document, dependencies=[Depends(require_admin)])
def add_document(item: DocumentIn) -> Document:
    doc, _created = index_service.add_document(item)
    return doc


@app.post("/api/crawl", response_model=CrawlResponse, dependencies=[Depends(require_admin)])
async def crawl(request: CrawlRequest) -> CrawlResponse:
    return await crawler.crawl(request)


# Single-container production serving: FastAPI serves the compiled React app when present.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = frontend_dist / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")
