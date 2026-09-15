# Benchmark

Measured locally with `python scripts/benchmark.py` on the generated 1,200-document corpus.

| Metric | Result |
| --- | ---: |
| Documents indexed | 1,200 |
| Vocabulary terms | 3,078 |
| Index build | 643.92 ms |
| Hybrid queries | 120 |
| p50 retrieval | 0.248 ms |
| p95 retrieval | 0.433 ms |
| average retrieval | 0.284 ms |
| max retrieval | 1.17 ms |

These numbers measure **in-process retrieval only** on a synthetic benchmark corpus. They are not production/network latency claims. Re-run the script on your own machine before quoting them elsewhere.
