# Nexus Search Quality Snapshot

This freezes the first post-corpus-hygiene production quality milestone.

## Phase 12.6 production snapshot

Benchmark: `eval/technical_search_v1.jsonl`  
Queries: 40  
Environment: Railway production

| Metric | Phase 12.1 | Post-hygiene Phase 12.5 | Phase 12.6 |
| --- | ---: | ---: | ---: |
| Behavioral policy pass | 100.0% | 97.5% | **100.0%** |
| Authoritative-domain coverage@20 | 82.1% | 100.0% | **100.0%** |
| MRR | 0.2500 | 0.4286 | **0.5357** |
| Recall@5 | 0.2143 | 0.3929 | **0.5000** |
| domain-proxy nDCG@5 | 0.2224 | 0.4009 | **0.5081** |

Phase 12.5 replaced stale/noisy canonical documentation chunks instead of loosening global evidence thresholds. Phase 12.6 added an ambiguity guard and targeted definition matching.

The final Phase 12.6 run passed all 40 behavioral rules. Definition queries for Kubernetes readiness, Redis TTL and React Hooks moved to answer paths, while fake Redis/Kubernetes identifiers, HTTP 799 and the idempotency regression continued to abstain.

## Interpretation

- Behavioral policy pass is a policy check, not factual accuracy.
- Authoritative-domain coverage@20 measures raw top-20 domain coverage.
- MRR / Recall@5 / nDCG@5 use domain-level proxy relevance labels in v1.
- These metrics are benchmark-specific, not generalized search accuracy.

Machine-readable snapshot: `eval/baselines/phase12_6_v1.json`.
