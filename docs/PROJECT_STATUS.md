# Nexus Project Status

## Demonstrated

- coordinator + 3 search shards
- custom BM25, semantic retrieval and RRF
- rendezvous-hashed placement
- PostgreSQL-backed shard recovery
- Kafka/Redpanda asynchronous indexing
- retry, idempotent handling and DLQ paths
- circuit breakers, timeouts and bounded fan-out
- process-local admission control
- readiness/liveness separation
- Kubernetes `kind` shard-failure tests
- Adaptive Search Autopilot
- trusted-source crawling and canonical ingestion
- URL-level refresh and stale chunk replacement
- exact identifier grounding
- sentence-level support checks
- intent-aware answerability
- heuristic agreement/conflict checks
- Evidence Inspector UI
- frozen 40-query v1 benchmark

## Frozen quality snapshot

```text
Behavioral policy pass           100.0%
Authoritative-domain coverage@20 100.0%
MRR                                0.5357
Recall@5                           0.5000
domain-proxy nDCG@5                0.5081
```

These are specific to `technical_search_v1` and the tested Railway deployment.

## Verified deployment benchmark

Across three 500-request Railway runs at concurrency 60:

```text
median throughput  82.45 req/s
median p50         714.69 ms
median p95         1035.38 ms
median p99         1508.70 ms
HTTP success       100% in those runs
```

## Deliberate limitations

- no serving replica groups yet
- no automated online shard rebalancing
- admission control is process-local
- v1 relevance uses domain-level proxy labels
- conflict detection is heuristic
- current corpus/deployment is a technical-search demo, not internet scale

Core v1 search behavior is frozen. Future work should focus on production properties and evaluation maturity rather than tuning thresholds against the same 40-query benchmark.
