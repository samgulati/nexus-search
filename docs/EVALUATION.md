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

## Retrieval diagnostics

Phase 12.2 adds `scripts/run_retrieval_diagnostics.py`. It runs both raw
`/api/search` and `/api/ask` for the same labeled query.

```bash
python3 scripts/run_retrieval_diagnostics.py \
  --base-url https://nexus-search-production.up.railway.app
```

The report records raw top-20 titles/domains/scores, the first rank of an
expected authoritative domain, the final answer decision, and citation domains.

`candidate_coverage_miss_top_20` does not prove a source is absent from the
corpus. It only means the expected authoritative domain did not enter the raw
top-20 candidate set; the source may be missing or ranked below K.

## Support-stage tracing

Phase 12.3 adds `scripts/run_support_trace.py`. It replays the local
`AnswerService` heuristics against raw candidates returned by `/api/search` and
records why each candidate survives or is removed.

Example:

```bash
python3 scripts/run_support_trace.py \
  --base-url https://nexus-search-production.up.railway.app \
  --case-id def-k8s-readiness \
  --case-id def-redis-ttl
```

Each candidate records raw retrieval scores, query relevance, dynamic
relevance floor, exact identifier checks, per-sentence support scores,
definition-intent checks, base authority, topical authority, and the first
pruning stage.

For exact diagnosis, run the trace script from the same commit that is deployed
to the target environment. The target supplies retrieval results, while the
local checkout supplies the evidence heuristics.

## Corpus precision pass

Phase 12.4 improves corpus precision without weakening evidence thresholds.

It adds canonical high-value seeds for evaluation-critical concepts, filters
probable localized documentation mirrors for the current English corpus, and
drops obvious navigation-only blocks such as Python navigation/theme text.

After deployment, re-ingest the focused sources with:

```bash
python3 scripts/reingest_eval_sources.py \
  --url https://nexus-search-production.up.railway.app \
  --pages 20
```

`ADMIN_TOKEN` must be exported locally. Never paste it into logs or chat.

Then rerun both retrieval diagnostics and the 40-case quality benchmark. The
goal is to improve passage/candidate quality while preserving adversarial and
out-of-domain abstentions.

## URL refresh and corpus hygiene

Phase 12.5 adds safe URL-level refresh semantics for trusted documentation.

A refresh only replaces a page after the fetch and extraction produced at least
one valid chunk. Replacement is atomic per URL on each shard: stale chunks for
that URL are removed, the current cleaned chunks are persisted transactionally,
and the in-memory index is rebuilt once. If persistence fails, the previous
in-memory corpus is restored.

Refresh metrics:
- `nexus_corpus_refresh_events_total{outcome=...}`
- `nexus_corpus_refresh_chunks_total{action=...}`

Refresh the evaluation-critical canonical pages with:

```bash
python3 scripts/refresh_eval_sources.py \
  --url https://nexus-search-production.up.railway.app \
  --timeout 600
```

`ADMIN_TOKEN` must stay in the local environment and must never be pasted into
logs, source control, or chat.

## Phase 12.6: ambiguity and definition precision

Phase 12.6 prevents context-poor recommendation prompts from becoming definitive
answers and adds targeted plural/title-context handling for definition queries.
Global evidence thresholds are unchanged, and mere mentions still do not count
as definitions.
