from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_final_package_files_exist():
    for rel in [
        "docs/PITCH_AND_DEMO.md",
        "docs/ARCHITECTURE_ONE_PAGER.md",
        "docs/PROJECT_STATUS.md",
    ]:
        assert (ROOT / rel).exists()

def test_pitch_preserves_non_claims():
    text = (ROOT / "docs" / "PITCH_AND_DEMO.md").read_text()
    assert "not exactly-once" in text.lower()
    assert "not generalized search accuracy" in text.lower()
    assert "Partial results are graceful degradation, not shard failover." in text

def test_project_status_contains_frozen_metrics():
    text = (ROOT / "docs" / "PROJECT_STATUS.md").read_text()
    for metric in ["100.0%", "0.5357", "0.5000", "0.5081"]:
        assert metric in text

def test_readme_links_final_package():
    text = (ROOT / "README.md").read_text()
    assert "docs/PITCH_AND_DEMO.md" in text
    assert "docs/ARCHITECTURE_ONE_PAGER.md" in text
    assert "docs/PROJECT_STATUS.md" in text
