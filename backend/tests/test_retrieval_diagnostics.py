from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_retrieval_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("nexus_retrieval_diagnostics", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def case():
    return {"preferred_domains": ["kubernetes.io"], "acceptable_domains": []}


def search(*domains):
    return {"results": [{"title": d, "url": f"https://{d}/docs", "score": 1.0} for d in domains]}


def ask(decision, *domains):
    return {
        "evidence": {"decision": decision, "agreement": "insufficient", "reasons": []},
        "citations": [{"title": d, "url": f"https://{d}/docs", "document_id": d} for d in domains],
    }


def test_first_judged_rank():
    assert MODULE.first_judged_rank(search("example.com", "kubernetes.io")["results"], case()) == 2


def test_candidate_coverage_miss_is_not_mislabeled_as_corpus_failure():
    result = MODULE.diagnose(case(), search("example.com"), ask("abstain"), 20)
    assert result == "candidate_coverage_miss_top_20"


def test_retrieved_then_abstained_is_post_retrieval_issue():
    result = MODULE.diagnose(case(), search("kubernetes.io", "example.com"), ask("abstain"), 20)
    assert result == "retrieved_then_filtered_or_answerability_abstain"


def test_authoritative_citation_reaches_answer():
    result = MODULE.diagnose(case(), search("kubernetes.io"), ask("answer", "kubernetes.io"), 20)
    assert result == "authoritative_evidence_reached_answer"


def test_behavior_only_case():
    no_labels = {"preferred_domains": [], "acceptable_domains": []}
    assert MODULE.diagnose(no_labels, search("example.com"), ask("abstain"), 20) == "behavior_only_abstain"
