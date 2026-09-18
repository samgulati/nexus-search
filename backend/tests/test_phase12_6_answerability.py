from __future__ import annotations

import asyncio

from app.models import SearchResponse, SearchResult
from app.services.answer_service import AnswerService


def r(doc_id, title, snippet, url, source):
    return SearchResult(
        id=doc_id, title=title, url=url, snippet=snippet, score=1.0,
        bm25_score=1.0, semantic_score=0.2, source=source,
    )


def sr(results):
    return SearchResponse(
        query="", mode="hybrid", took_ms=1.0, total=len(results), results=results
    )


def test_ambiguous_retry_never_definitive():
    svc = AnswerService()
    out = asyncio.run(svc.answer_from_search(
        "Should I retry?",
        sr([
            r(
                "1", "Retry guidance",
                "Retries can help after transient failures, but repeated retries can increase load and latency.",
                "https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/",
                "trusted-docs:aws.amazon.com",
            ),
            r(
                "2", "Handling overload",
                "Retry behavior depends on failure mode, request semantics, and backoff strategy.",
                "https://sre.google/sre-book/handling-overload/",
                "trusted-docs:sre.google",
            ),
        ]),
        result_limit=5,
    ))
    assert out.evidence.decision in {"abstain", "answer_with_caveat"}
    assert any("underspecified" in reason.lower() for reason in out.evidence.reasons)


def test_specific_retry_not_ambiguous():
    assert not AnswerService._is_underspecified_query(
        "Should every failed HTTP request be retried?"
    )


def test_short_definition_not_ambiguous():
    assert not AnswerService._is_underspecified_query("What is CSRF?")


def test_plural_interface_definition():
    assert AnswerService._sentence_defines_subject(
        "What is an interface in Java?",
        "Interfaces specify contracts that classes can implement and are reference types.",
    )


def test_plural_probe_definition():
    assert AnswerService._sentence_defines_subject(
        "What is a Kubernetes readiness probe?",
        "Readiness probes indicate whether a container is ready to accept traffic.",
    )


def test_react_title_context_definition():
    result = r(
        "hook", "Built-in React Hooks",
        "Hooks let you use different React features from your components.",
        "https://react.dev/reference/react/hooks", "trusted-docs:react.dev",
    )
    assert AnswerService._prune_definition_results("What is a React Hook?", [result]) == [result]


def test_python_iterator_support():
    result = r(
        "iterator", "Python Tutorial — Iterators",
        "An iterator is an object that represents a stream of data.",
        "https://docs.python.org/3/tutorial/classes.html",
        "trusted-docs:docs.python.org",
    )
    assert AnswerService._supporting_sentences("What is an iterator in Python?", result)


def test_idempotency_mention_still_rejected():
    assert not AnswerService._sentence_defines_subject(
        "What is idempotency in distributed systems?",
        "Sagas require explicit failure states, idempotency, retries, and compensation semantics.",
    )
