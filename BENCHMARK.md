# Benchmarks

Nexus keeps algorithm-level and deployment-level measurements separate so the numbers are not presented as something they are not.

## 1. Single-node algorithm benchmark

Measured locally with `python scripts/benchmark.py` on a generated 1,200-document corpus.

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

These values measure **in-process single-node retrieval only** on a synthetic corpus. They exclude HTTP, TLS, coordinator fan-out and network latency.

## 2. Live three-shard deployment validation

Target: `https://nexus-search-production.up.railway.app`

Topology: one public coordinator + three private Railway shards

Corpus: 20 demo documents

Load: 200 search requests, concurrency 20, six representative queries

| Metric | Result |
| --- | ---: |
| Successful requests | 200 / 200 |
| Errors | 0 |
| Throughput | 81.15 req/s |
| Client p50 | 237.50 ms |
| Client p95 | 326.80 ms |
| Client p99 | 362.03 ms |
| Client average | 232.44 ms |
| Internal query p50 | 92.84 ms |
| Internal query p95 | 171.34 ms |
| Internal query p99 | 199.73 ms |
| Internal query average | 95.36 ms |

Client-observed latency includes HTTPS/network overhead. The API `took_ms` figures cover the coordinator's distributed search path. Because this run uses only 20 documents, it validates deployment behavior and concurrency rather than large-corpus scalability.

## 3. Shard failure injection

One of the three configured shard URLs was temporarily replaced with an unreachable Railway-private hostname. No shard service was modified.

| Metric | Healthy cluster | Injected failure | Restored |
| --- | ---: | ---: | ---: |
| Healthy shards | 3 / 3 | 2 / 3 | 3 / 3 |
| Visible demo documents | 20 | 13 | 20 |
| Search HTTP status | 200 | 200 | 200 |
| Failure-test search latency | — | 14.513 ms | 16.262 ms |
| Result shard IDs | 0, 1, 2 | 0, 1 | 0, 1, 2 |

This validates **graceful partial-result behavior**, not replica failover: when a shard is unavailable, the coordinator returns results from surviving shards instead of failing the entire query. Replica groups and automatic shard failover remain future work.

## Reproduce

```bash
# local algorithm benchmark
python scripts/benchmark.py

# coordinator HTTP benchmark
python scripts/cluster_benchmark.py --base-url https://nexus-search-production.up.railway.app
```

Benchmark results vary by region, network conditions and Railway resource allocation. Re-run them before quoting new numbers.
