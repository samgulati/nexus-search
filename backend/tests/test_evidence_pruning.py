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


def test_http_503_prunes_weak_tail_and_keeps_direct_support():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    results = [
        r(
            "mdn",
            "HTTP 503 Service Unavailable and Retry-After",
            "HTTP 503 Service Unavailable means a server is temporarily unable to handle a request, often because of overload or maintenance.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
            "trusted-docs:developer.mozilla.org",
        ),
        r(
            "prom",
            "Blog | Prometheus — What does 1.0 mean for you?",
            "A Prometheus service may be unavailable while a component is restarted.",
            "https://prometheus.io/blog/",
            "trusted-docs:prometheus.io",
            score=5.0,
        ),
        r(
            "owasp",
            "Authorization Testing Automation",
            "format errorMessageTplForIncorrectReturnCode service serviceResponseCode getContentType.",
            "https://cheatsheetseries.owasp.org/x",
            "trusted-docs:cheatsheetseries.owasp.org",
            score=4.0,
        ),
    ]

    ranked = svc._rank_relevant_results(query, results, limit=5)
    supported = svc._prune_to_supported_results(query, ranked)

    assert [x.id for x in supported] == ["mdn"]
    answer = svc._extractive_answer(query, supported)
    assert "temporarily unable" in answer
    assert "getContentType" not in answer


def test_identifier_query_requires_identifier_in_surviving_result():
    svc = AnswerService()
    query = "HTTP 503 Service Unavailable"
    results = [
        r(
            "good",
            "HTTP 503 Service Unavailable",
            "HTTP 503 indicates temporary service unavailability.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
            "trusted-docs:developer.mozilla.org",
        ),
        r(
            "bad",
            "HTTP service availability",
            "Servers can become temporarily unavailable during maintenance.",
            "https://prometheus.io/blog/",
            "trusted-docs:prometheus.io",
        ),
    ]
    ranked = svc._rank_relevant_results(query, results, limit=5)
    assert [x.id for x in ranked] == ["good"]


def test_judgment_query_requires_tradeoff_support_for_sentence():
    svc = AnswerService()
    query = "Should every distributed system always use exactly-once processing?"

    capability_only = r(
        "kafka",
        "Kafka Streams exactly-once processing",
        "Kafka Streams supports exactly-once processing for stateful stream processing applications.",
        "https://kafka.apache.org/documentation/streams/",
        "trusted-docs:kafka.apache.org",
    )
    tradeoff = r(
        "tradeoff",
        "Exactly-once processing tradeoffs",
        "Exactly-once guarantees can add coordination overhead and latency, so applications should choose them when duplicate processing is unacceptable.",
        "https://sre.google/sre-book/",
        "trusted-docs:sre.google",
    )

    assert svc._supporting_sentences(query, capability_only) == []
    assert svc._supporting_sentences(query, tradeoff)


def test_answer_abstains_when_only_capability_not_tradeoff_is_retrieved():
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
    assert response.citations == []
    assert response.model == "evidence-gate"
