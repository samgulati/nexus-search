from __future__ import annotations

import asyncio

from app.models import SearchResponse, SearchResult
from app.services.answer_service import AnswerService


def result(doc_id: str, title: str, snippet: str, url: str, source: str) -> SearchResult:
    return SearchResult(
        id=doc_id,
        title=title,
        url=url,
        snippet=snippet,
        score=1.0,
        bm25_score=1.0,
        semantic_score=0.5,
        source=source,
    )


def make_search(results: list[SearchResult]) -> SearchResponse:
    return SearchResponse(
        query="",
        mode="hybrid",
        took_ms=5.0,
        total=len(results),
        results=results,
    )


def test_high_authority_evidence_can_answer():
    svc = AnswerService()
    s = make_search([
        result(
            "1",
            "HTTP 503 Service Unavailable",
            "HTTP 503 means the server is temporarily unable to handle the request.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
            "trusted-docs:developer.mozilla.org",
        ),
        result(
            "2",
            "Kubernetes readiness probes",
            "Readiness probes keep unavailable workloads from receiving traffic.",
            "https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/",
            "trusted-docs:kubernetes.io",
        ),
    ])
    ev = svc._evaluate_evidence("what does HTTP 503 service unavailable mean", s)
    assert ev.decision in {"answer", "answer_with_caveat"}
    # Phase 11C-2 uses topic-specific authority, so the unrelated Kubernetes
    # source should no longer keep the average authority near 1.0.
    assert ev.authority >= 0.75
    assert ev.relevance >= 0.40
    assert ev.independent_sources == 2


def test_low_coverage_abstains():
    svc = AnswerService()
    s = make_search([
        result(
            "1",
            "HTTP caching",
            "Caches can reuse stored HTTP responses.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching",
            "trusted-docs:developer.mozilla.org",
        )
    ])
    ev = svc._evaluate_evidence(
        "how does kubernetes leader election interact with postgres failover", s
    )
    assert ev.decision == "abstain"
    assert ev.coverage < 0.30


def test_answer_service_blocks_generation_when_evidence_is_insufficient():
    svc = AnswerService()
    s = make_search([
        result(
            "1",
            "HTTP caching",
            "Caches can reuse stored HTTP responses.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching",
            "trusted-docs:developer.mozilla.org",
        )
    ])
    response = asyncio.run(
        svc.answer_from_search(
            "how does kubernetes leader election interact with postgres failover",
            s,
        )
    )
    assert response.model == "evidence-gate"
    assert response.evidence is not None
    assert response.evidence.decision == "abstain"
    assert "does not generate unsupported answers" in response.answer
