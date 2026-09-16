# Nexus Benchmarks

Nexus keeps algorithm-level and deployment-level measurements separate. No benchmark below should be interpreted as a universal capacity claim.

## 1. Synthetic in-process retrieval

Measured with `python scripts/benchmark.py` on a generated 1,200-document corpus.

| Metric | Result |
| --- | ---: |
| Documents indexed | 1,200 |
| Vocabulary terms | 3,078 |
| Index build | 643.92 ms |
| Hybrid queries | 120 |
| p50 retrieval | 0.248 ms |
| p95 retrieval | 0.433 ms |
| Average retrieval | 0.284 ms |
| Max retrieval | 1.17 ms |

This measures only in-process single-node retrieval. It excludes HTTP, TLS, coordinator fan-out, network latency, and production infrastructure.

## 2. Earlier three-shard deployment smoke test

Target: `https://nexus-search-production.up.railway.app`

Topology: one public coordinator + three private Railway shards.

Corpus: 20 demo documents.

Load: 200 search requests at concurrency 20.

| Metric | Result |
| --- | ---: |
| Successful requests | 200 / 200 |
| Errors | 0 |
| Throughput | 81.15 req/s |
| Client p50 | 237.50 ms |
| Client p95 | 326.80 ms |
| Client p99 | 362.03 ms |
| Internal query p50 | 92.84 ms |
| Internal query p95 | 171.34 ms |
| Internal query p99 | 199.73 ms |

This was an earlier deployment smoke test. It is retained for historical comparison but is not the primary current benchmark.

## 3. Current reproducible HTTP benchmark

Harness:

```bash
PYTHONPATH=backend python3 scripts/benchmark_http.py \
  --base-url https://nexus-search-production.up.railway.app \
  --requests 500 \
  --concurrency 40,60,80,100 \
  --warmup 10 \
  --output benchmark-results.json
```

The harness records total completed-request throughput, p50/p95/p99 client-observed latency, HTTP 200 count, deliberate HTTP 503 load-shedding count, and unexpected errors.

Three repeated runs were executed with 500 requests at each concurrency level. Every recorded request in these three runs completed with HTTP 200; there were no 503 rejections and no unexpected errors.

### Median across the three repeated runs

| Concurrency | Median throughput | Median p50 | Median p95 | Median p99 |
| ---: | ---: | ---: | ---: | ---: |
| 40 | 69.94 req/s | 531.99 ms | 854.84 ms | 943.98 ms |
| 60 | **82.45 req/s** | 714.69 ms | **1,035.38 ms** | 1,508.70 ms |
| 80 | 68.58 req/s | 1,003.22 ms | 2,392.70 ms | 3,221.01 ms |
| 100 | 71.37 req/s | 982.21 ms | 2,578.49 ms | 2,849.47 ms |

### Individual throughput runs

| Concurrency | Run 1 | Run 2 | Run 3 |
| ---: | ---: | ---: | ---: |
| 40 | 78.00 | 68.93 | 69.94 |
| 60 | 86.62 | 82.45 | 76.20 |
| 80 | 68.58 | 71.40 | 65.58 |
| 100 | 92.81 | 66.42 | 71.37 |

The throughput/latency curve shows the clearest knee around concurrency 60 in these runs. Increasing concurrency to 80 produced lower median throughput and substantially higher tail latency.

The single 92.81 req/s observation at concurrency 100 should not be presented as sustained capacity because the same level showed much lower throughput in the other two runs.

## 4. Resilience validation

### Coordinator shard failure injection

A slow/failing shard target was introduced while a healthy shard remained available. The CI scenario validates:

- partial-result continuity from healthy shards;
- circuit opening after the configured failure threshold;
- fail-fast rejection while the circuit is open;
- half-open recovery after the recovery interval;
- return to healthy operation after the shard becomes available.

This demonstrates failure isolation in the tested scenario. It is not replica failover.

### Admission-control integration

A CI test runs the coordinator with `MAX_INFLIGHT_REQUESTS=2` and submits 20 concurrent search requests against a deliberately slow shard path. It verifies that admitted requests can complete while excess requests are rejected with HTTP 503 and `Retry-After: 1`.

This validates application-level overload shedding in the controlled CI scenario.

### Kubernetes failure recovery

The `scripts/k8s_e2e.sh` test creates a real ephemeral `kind` cluster and verifies:

- all three shards healthy -> coordinator readiness HTTP 200;
- one shard removed -> readiness remains HTTP 200 at 2/3 shards and search still returns HTTP 200;
- two shards removed -> readiness becomes HTTP 503 at 1/3 shards with a 2-shard minimum;
- restored shards -> readiness returns to HTTP 200 at 3/3 shards.

## Interpretation rules

When quoting Nexus benchmark numbers:

1. State whether the number is in-process or client-observed HTTP.
2. Include the request count and concurrency.
3. State that the deployment measurements are environment-specific.
4. Do not call partial-result behavior "failover" unless replicas are actually added.
5. Do not describe the highest single-run throughput as sustained capacity.
6. Do not imply the Railway production benchmark triggered admission control; it did not return 503s in the recorded runs.
