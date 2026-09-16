from __future__ import annotations

import asyncio

from app.models import SearchResponse, SearchResult
from app.services.answer_service import AnswerService


def r(doc_id: str, title: str, snippet: str, url: str, source: str, score: float = 1.0):
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


def test_http_503_prefers_direct_mdn_evidence_over_generic_authoritative_docs():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    results = [
        r(
            "noise",
            "Blog | Prometheus — Custom service discovery",
            "A service can be discovered through etcd.",
            "https://prometheus.io/blog/",
            "trusted-docs:prometheus.io",
            5.0,
        ),
        r(
            "mdn",
            "503 Service Unavailable - HTTP | MDN",
            "The HTTP 503 Service Unavailable server error response status code indicates that the server is not ready to handle the request.",
            "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
            "trusted-docs:developer.mozilla.org",
            1.0,
        ),
    ]

    ranked = svc._rank_relevant_results(query, results, limit=5)
    assert ranked
    assert ranked[0].id == "mdn"


def test_unrelated_authoritative_sources_do_not_count_as_evidence():
    svc = AnswerService()
    query = "How does photosynthesis work inside a sunflower leaf?"
    search = sr([
        r(
            "otel",
            "Community | OpenTelemetry",
            "Learn how the project community works and how to contribute.",
            "https://opentelemetry.io/community/",
            "trusted-docs:opentelemetry.io",
        ),
        r(
            "docker",
            "Docker guides",
            "Learn how containers work with development workflows.",
            "https://docs.docker.com/guides/",
            "trusted-docs:docs.docker.com",
        ),
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision == "abstain"
    assert response.evidence.relevant_evidence_count == 0
    assert response.evidence.discarded_results == 2
    assert response.citations == []


def test_exactly_once_evidence_is_qualified_when_tradeoff_evidence_is_thin():
    svc = AnswerService()
    query = "Should every distributed system always use exactly-once processing?"
    search = sr([
        r(
            "kafka",
            "Kafka Streams processing guarantees",
            "Kafka Streams supports exactly-once processing semantics for stateful stream processing.",
            "https://kafka.apache.org/documentation/streams/",
            "trusted-docs:kafka.apache.org",
        ),
        r(
            "cap",
            "CAP theorem",
            "A distributed system facing a network partition must make consistency and availability tradeoffs.",
            "https://en.wikipedia.org/wiki/CAP_theorem",
            "manual",
        ),
    ])

    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    assert response.evidence is not None
    assert response.evidence.decision in {"answer_with_caveat", "abstain"}
    assert response.evidence.relevant_evidence_count <= 2


def test_topic_authority_is_lower_for_unrelated_domain():
    svc = AnswerService()
    query = "HTTP 503 Service Unavailable"
    mdn = r(
        "mdn",
        "503 Service Unavailable - HTTP | MDN",
        "HTTP 503 means the server is temporarily unavailable.",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
        "trusted-docs:developer.mozilla.org",
    )
    prom = r(
        "prom",
        "Prometheus service discovery",
        "Service discovery configuration.",
        "https://prometheus.io/docs/prometheus/latest/configuration/configuration/",
        "trusted-docs:prometheus.io",
    )
    assert svc._topic_authority_score(query, mdn) > svc._topic_authority_score(query, prom)
