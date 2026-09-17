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


def test_definition_query_rejects_mentions_that_do_not_define_subject():
    svc = AnswerService()
    query = "What is idempotency in distributed systems?"
    search = sr([
        r(
            "prom",
            "Distributed systems background",
            "My background is in distributed systems and large-scale search infrastructure.",
            "https://prometheus.io/blog/",
            "trusted-docs:prometheus.io",
        ),
        r(
            "saga",
            "Saga Pattern",
            "Sagas require explicit failure states, idempotency, retries, and compensation semantics.",
            "https://microservices.io/patterns/data/saga.html",
            "trusted-docs:microservices.io",
        ),
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision == "abstain"
    assert response.citations == []
    assert response.model == "evidence-gate"
    assert any(
        "definition query" in reason
        for reason in response.evidence.reasons
    )


def test_definition_query_accepts_direct_definition():
    svc = AnswerService()
    query = "What is idempotency in distributed systems?"
    search = sr([
        r(
            "direct",
            "Idempotency",
            "Idempotency is a property where repeating the same operation produces the same intended effect.",
            "https://example.com/idempotency",
            "trusted-docs:example.com",
        )
    ])

    assert svc._is_definition_query(query) is True
    assert svc._sentence_defines_subject(query, search.results[0].snippet) is True
    assert svc._prune_definition_results(query, search.results)


def test_http_503_mean_query_remains_definition_answerable():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    sentence = (
        "HTTP 503 Service Unavailable means a server is temporarily unable "
        "to handle a request, often because of overload or maintenance."
    )

    assert svc._is_definition_query(query) is True
    assert svc._sentence_defines_subject(query, sentence) is True


def test_low_relevance_and_low_authority_force_abstention():
    svc = AnswerService()
    query = "How does idempotency prevent duplicate payments?"
    search = sr([
        r(
            "weak",
            "Distributed transaction notes",
            "Idempotency may be one concern in a broad distributed architecture discussion.",
            "https://example.com/notes",
            "manual",
        ),
        r(
            "weak2",
            "Payment systems notes",
            "Distributed payment systems can contain retries and duplicate operations.",
            "https://example.net/notes",
            "manual",
        ),
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision == "abstain"


def test_identifier_definition_allows_compact_http_503_wording():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"

    assert svc._sentence_defines_subject(
        query,
        "HTTP 503 means the server is temporarily unable to handle requests.",
    )
    assert svc._sentence_defines_subject(
        query,
        "HTTP 503 indicates that the server is temporarily unable to handle requests.",
    )


def test_definition_mention_without_definition_marker_is_rejected():
    svc = AnswerService()
    query = "What is idempotency in distributed systems?"

    assert not svc._sentence_defines_subject(
        query,
        "Sagas require explicit failure states, idempotency, retries, and compensation semantics.",
    )
