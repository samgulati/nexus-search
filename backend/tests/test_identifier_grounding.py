from __future__ import annotations

import asyncio

from app.models import SearchResponse, SearchResult
from app.services.answer_service import AnswerService


def r(doc_id, title, snippet, url, source, score=1.0):
    return SearchResult(
        id=doc_id,
        title=title,
        url=url,
        snippet=snippet,
        score=score,
        bm25_score=score,
        semantic_score=0.2,
        source=source,
    )


def sr(results):
    return SearchResponse(
        query="",
        mode="hybrid",
        took_ms=1.0,
        total=len(results),
        results=results,
    )


def test_fake_redis_command_requires_exact_identifier_support():
    svc = AnswerService()
    query = "What does the Redis PERMASTORE command do?"
    search = sr([
        r(
            "redis",
            "Redis persistence",
            "Redis supports persistence using snapshots and append-only files.",
            "https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/",
            "trusted-docs:redis.io",
        )
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))

    assert response.evidence is not None
    assert response.evidence.decision == "abstain"
    assert response.citations == []


def test_real_uppercase_identifier_can_survive_when_exactly_present():
    svc = AnswerService()
    query = "What is CSRF?"
    result = r(
        "csrf",
        "Cross-Site Request Forgery Prevention",
        "CSRF is an attack that tricks a user into submitting an unwanted request.",
        "https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html",
        "trusted-docs:cheatsheetseries.owasp.org",
    )

    ranked = svc._rank_relevant_results(query, [result], limit=5)
    assert ranked == [result]


def test_http_503_identifier_terms_are_exact():
    svc = AnswerService()
    terms = svc._explicit_identifier_terms(
        "What does HTTP 503 Service Unavailable mean?"
    )
    assert "http" in terms
    assert "503" in terms
