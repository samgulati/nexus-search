# Nexus quality evaluation

Nexus uses a versioned labeled set in `eval/technical_search_v1.jsonl` so changes are measured instead of tuned against a few memorable queries.

The first version spans definition, procedural, identifier, judgment/tradeoff, multi-source, out-of-domain, ambiguous, adversarial, and regression cases.

The runner reports behavioral pass rate plus domain-level Recall@K, MRR, and nDCG@K. Preferred authoritative domains have gain 2 and acceptable domains gain 1. These domain labels are a first-stage proxy, not passage-level relevance judgments.

Run locally:

```bash
python3 scripts/run_quality_eval.py --base-url http://localhost:8000
```

Run production:

```bash
python3 scripts/run_quality_eval.py --base-url https://nexus-search-production.up.railway.app
```

Outputs are `eval/latest_report.json` and `eval/latest_report.md`.

Use `--category judgment` for one category or `--limit 5` for a smoke run.

Do not present the initial numbers as generalized search accuracy. The v1 set is small and manually labeled. Its purpose is regression detection and diagnosis. Quality misses should be classified as corpus coverage, retrieval/ranking, answerability/evidence gating, citation selection, or label problems. Passage-level human labels should be added before accuracy claims or hard CI quality gates.
