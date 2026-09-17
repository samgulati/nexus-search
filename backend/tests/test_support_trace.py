from __future__ import annotations

import importlib.util
from pathlib import Path

from app.models import SearchResult


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_support_trace.py"
SPEC = importlib.util.spec_from_file_location("nexus_support_trace", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def r(title: str, snippet: str, url: str = "https://kubernetes.io/docs/test"):
    return SearchResult(
        id="x",
        title=title,
        url=url,
        snippet=snippet,
        score=1.0,
        bm25_score=1.0,
        semantic_score=0.2,
        source="trusted-docs:kubernetes.io",
    )


def test_trace_exposes_support_failure():
    query = "How do I configure a readiness probe in Kubernetes?"
    result = r(
        "Kubernetes readiness probe",
        "Kubernetes uses readiness probes for application availability.",
    )
    _best, floor = MODULE.compute_relative_floor(query, [result])
    trace = MODULE.stage_trace(query, result, raw_rank=1, relative_floor=floor)

    assert "support" in trace
    assert "authority" in trace
    assert trace["raw_rank"] == 1
    assert trace["final_stage"] in {
        "pruned_sentence_support",
        "survives_local_evidence_pruning",
    }


def test_trace_rejects_fake_identifier():
    query = "What does the Redis PERMASTORE command do?"
    result = SearchResult(
        id="redis",
        title="Redis persistence",
        url="https://redis.io/docs/persistence/",
        snippet="Redis supports persistence using snapshots and append-only files.",
        score=1.0,
        bm25_score=1.0,
        semantic_score=0.2,
        source="trusted-docs:redis.io",
    )
    _best, floor = MODULE.compute_relative_floor(query, [result])
    trace = MODULE.stage_trace(query, result, raw_rank=1, relative_floor=floor)

    assert trace["identifier"]["pass"] is False
    assert trace["final_stage"] == "pruned_identifier_grounding"


def test_definition_trace_exposes_definition_check():
    query = "What is a Kubernetes readiness probe?"
    result = r(
        "Kubernetes readiness probe",
        "A readiness probe is used to determine whether a container is ready to accept traffic.",
    )
    _best, floor = MODULE.compute_relative_floor(query, [result])
    trace = MODULE.stage_trace(query, result, raw_rank=1, relative_floor=floor)

    assert trace["definition"]["is_definition_query"] is True
    assert "sentence_checks" in trace["definition"]
