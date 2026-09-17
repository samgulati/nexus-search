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


def test_strong_first_party_factual_source_can_answer_without_second_source():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    search = sr([
        r(
            "mdn",
            "HTTP 503 Service Unavailable and Retry-After",
            "HTTP 503 Service Unavailable means a server is temporarily unable to handle a request, often because of overload or maintenance.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
            "trusted-docs:developer.mozilla.org",
        )
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision == "answer"
    assert response.evidence.independent_sources == 1
    assert not any("fewer than two" in reason for reason in response.evidence.reasons)


def test_judgment_query_does_not_get_single_source_override():
    svc = AnswerService()
    query = "Should every distributed system always use exactly-once processing?"
    search = sr([
        r(
            "tradeoff",
            "Exactly-once processing tradeoffs",
            "Exactly-once guarantees can add coordination overhead and latency, so applications should choose them when duplicate processing is unacceptable.",
            "https://sre.google/sre-book/",
            "trusted-docs:sre.google",
        )
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision != "answer"


def test_claim_support_rejection_reason_is_specific():
    svc = AnswerService()
    query = "Should every distributed system always use exactly-once processing?"
    search = sr([
        r(
            "kafka",
            "Kafka Streams exactly-once processing",
            "Kafka Streams supports exactly-once processing for stateful stream processing applications.",
            "https://kafka.apache.org/documentation/streams/",
            "trusted-docs:kafka.apache.org",
        )
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision == "abstain"
    assert any("claim-level support" in reason for reason in response.evidence.reasons)
