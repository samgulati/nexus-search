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
    return SearchResponse(query="", mode="hybrid", took_ms=1.0, total=len(results), results=results)


def test_single_strong_source_reports_single_source_not_conflict():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    search = sr([r(
        "mdn",
        "HTTP 503 Service Unavailable",
        "HTTP 503 Service Unavailable means a server is temporarily unable to handle a request during overload or maintenance.",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503",
        "trusted-docs:developer.mozilla.org",
    )])
    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    ev = response.evidence
    assert ev is not None
    assert ev.agreement == "single_source"
    assert ev.conflict_detected is False
    assert ev.supporting_sources == 1
    assert ev.conflicting_sources == 0
    assert ev.decision == "answer"


def test_two_sources_with_same_claim_report_agreement():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    search = sr([
        r("a", "HTTP 503 Service Unavailable", "HTTP 503 means the server is temporarily unable to handle requests because of overload or maintenance.", "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503", "trusted-docs:developer.mozilla.org"),
        r("b", "HTTP 503 Service Unavailable", "HTTP 503 indicates that the server is temporarily unable to handle requests due to overload or maintenance.", "https://example.com/http-503", "trusted-docs:example.com"),
    ])
    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    ev = response.evidence
    assert ev is not None
    assert ev.agreement == "agreement"
    assert ev.conflict_detected is False
    assert ev.supporting_sources >= 2
    assert ev.conflicting_sources == 0


def test_comparable_opposite_claims_trigger_conflict_and_caveat():
    svc = AnswerService()
    query = "Is exactly-once processing required for distributed payments?"
    search = sr([
        r("a", "Exactly-once processing for payments", "Exactly-once processing is required for distributed payments when duplicate charges cannot be tolerated.", "https://sre.google/sre-book/", "trusted-docs:sre.google"),
        r("b", "Exactly-once processing for payments", "Exactly-once processing is not required for distributed payments when idempotency prevents duplicate charges.", "https://kafka.apache.org/documentation/", "trusted-docs:kafka.apache.org"),
    ])
    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    ev = response.evidence
    assert ev is not None
    assert ev.conflict_detected is True
    assert ev.agreement == "mixed"
    assert ev.conflicting_sources >= 2
    assert ev.decision == "answer_with_caveat"
    assert response.answer.startswith("Relevant sources contain potentially conflicting claims")


def test_different_aspects_are_mixed_not_false_conflict():
    svc = AnswerService()
    query = "What does HTTP 503 Service Unavailable mean?"
    search = sr([
        r("a", "HTTP 503 Service Unavailable", "HTTP 503 means the server is temporarily unable to handle requests because of overload.", "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503", "trusted-docs:developer.mozilla.org"),
        r("b", "HTTP Retry-After", "A Retry-After header can tell clients how long to wait before making another request after a temporary error.", "https://example.com/retry-after", "trusted-docs:example.com"),
    ])
    response = asyncio.run(svc.answer_from_search(query, search, result_limit=5))
    ev = response.evidence
    assert ev is not None
    assert ev.conflict_detected is False
    assert ev.agreement in {"mixed", "single_source"}


def test_condition_negation_does_not_flip_main_claim_polarity():
    svc = AnswerService()
    query = "Is exactly-once processing required for distributed payments?"

    positive = (
        "Exactly-once processing is required for distributed payments "
        "when duplicate charges cannot be tolerated."
    )
    negative = (
        "Exactly-once processing is not required for distributed payments "
        "when idempotency prevents duplicate charges."
    )

    assert svc._sentence_polarity(query, positive) == 1
    assert svc._sentence_polarity(query, negative) == -1


def test_opposite_payment_claims_are_comparable_before_polarity_check():
    svc = AnswerService()
    query = "Is exactly-once processing required for distributed payments?"

    left = (
        "Exactly-once processing is required for distributed payments "
        "when duplicate charges cannot be tolerated."
    )
    right = (
        "Exactly-once processing is not required for distributed payments "
        "when idempotency prevents duplicate charges."
    )

    left_tokens = svc._claim_tokens(query, left)
    right_tokens = svc._claim_tokens(query, right)

    assert {"duplicate", "charges"} <= left_tokens
    assert {"duplicate", "charges"} <= right_tokens
    assert svc._claims_comparable(left_tokens, right_tokens) is True
    assert svc._sentence_polarity(query, left) == 1
    assert svc._sentence_polarity(query, right) == -1


def test_claim_tokens_normalize_trailing_technical_punctuation():
    svc = AnswerService()
    query = "Is exactly-once processing required for distributed payments?"

    tokens = svc._claim_tokens(
        query,
        "Idempotency prevents duplicate charges.",
    )

    assert "charges" in tokens
    assert "charges." not in tokens
