from __future__ import annotations
import importlib.util
from pathlib import Path
MODULE_PATH=Path(__file__).resolve().parents[2]/"scripts"/"run_quality_eval.py"
SPEC=importlib.util.spec_from_file_location("nexus_quality_eval",MODULE_PATH)
MODULE=importlib.util.module_from_spec(SPEC); assert SPEC.loader; SPEC.loader.exec_module(MODULE)

def c(): return {"preferred_domains":["kubernetes.io"],"acceptable_domains":["docs.docker.com"]}

def test_rr():
    assert MODULE.reciprocal_rank(["x.com","kubernetes.io"],c())==0.5

def test_recall():
    assert MODULE.recall_at_k(["kubernetes.io","x.com"],c(),2)==0.5
    assert MODULE.recall_at_k(["kubernetes.io","docs.docker.com"],c(),2)==1.0

def test_ndcg():
    a=MODULE.ndcg_at_k(["kubernetes.io","docs.docker.com"],c(),2)
    b=MODULE.ndcg_at_k(["docs.docker.com","kubernetes.io"],c(),2)
    assert a==1.0 and b<a

def test_unjudged_is_none():
    x={"preferred_domains":[],"acceptable_domains":[]}
    assert MODULE.reciprocal_rank(["x.com"],x) is None
    assert MODULE.recall_at_k(["x.com"],x,5) is None
    assert MODULE.ndcg_at_k(["x.com"],x,5) is None

def test_citation_rules():
    assert MODULE.citation_rule_ok({"must_cite":False},{"citations":[]})
    assert not MODULE.citation_rule_ok({"must_cite":False},{"citations":[{"url":"x"}]})
    assert MODULE.citation_rule_ok({"must_cite":True},{"citations":[{"url":"x"}]})


def test_ndcg_duplicate_domain_cannot_exceed_one():
    case={"preferred_domains":["redis.io"],"acceptable_domains":[]}
    score=MODULE.ndcg_at_k(["redis.io","redis.io","redis.io"],case,5)
    assert score == 1.0
