from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_quality_baseline_is_valid():
    data = json.loads((ROOT / 'eval' / 'baselines' / 'phase12_6_v1.json').read_text())
    assert data['queries'] == 40
    assert data['behavior_pass_rate'] == 1.0
    assert 0.0 <= data['mrr'] <= 1.0
    assert 0.0 <= data['recall_at_5'] <= 1.0
    assert 0.0 <= data['ndcg_at_5'] <= 1.0


def test_evidence_inspector_is_present():
    source = (ROOT / 'frontend' / 'src' / 'main.jsx').read_text()
    assert 'Evidence Inspector' in source
    assert 'EvidenceMetric' in source
    assert 'answer?.evidence' in source


def test_demo_guide_preserves_non_claims():
    guide = (ROOT / 'docs' / 'DEMO_GUIDE.md').read_text()
    assert 'Do not claim exactly-once ingestion' in guide
    assert 'Do not claim shard replication/failover' in guide
