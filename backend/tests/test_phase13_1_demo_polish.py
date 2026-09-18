from __future__ import annotations

import asyncio
from pathlib import Path

from app.models import SearchResponse, SearchResult
from app.services.answer_service import AnswerService

ROOT = Path(__file__).resolve().parents[2]

def r(doc_id, title, snippet, url, source):
    return SearchResult(
        id=doc_id, title=title, url=url, snippet=snippet,
        score=1.0, bm25_score=1.0, semantic_score=0.2, source=source,
    )

def sr(results):
    return SearchResponse(query="", mode="hybrid", took_ms=1.0, total=len(results), results=results)

def test_underspecified_retry_abstains():
    svc = AnswerService()
    search = sr([
        r("a", "Retry guidance",
          "Retries can recover transient failures but repeated retries can amplify overload.",
          "https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/",
          "trusted-docs:aws.amazon.com"),
        r("b", "Handling overload",
          "Retry behavior depends on the failure mode and operation semantics.",
          "https://sre.google/sre-book/handling-overload/",
          "trusted-docs:sre.google"),
    ])
    out = asyncio.run(svc.answer_from_search("Should I retry?", search, result_limit=5))
    assert out.evidence.decision == "abstain"
    assert "too underspecified" in out.answer.lower()

def test_frontend_marks_candidate_vs_used_evidence():
    source = (ROOT / "frontend" / "src" / "main.jsx").read_text()
    assert "Retrieval candidates" in source
    assert "used as evidence" in source
    assert "candidate only" in source
    assert "citedDocumentIds" in source
