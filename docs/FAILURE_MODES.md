# Nexus Failure Modes

This document distinguishes behavior that is implemented and tested from behavior that would require additional architecture.

| Failure | Current behavior | Verified where |
| --- | --- | --- |
| One search shard slow/unavailable | Coordinator can return results from healthy shards | resilience CI + Kubernetes E2E |
| Repeated shard failures | Per-shard circuit opens and temporarily rejects calls | resilience CI |
| Shard becomes healthy again | Half-open probe can restore closed state | resilience CI |
| Public request concurrency exceeds process gate | Excess expensive requests receive HTTP 503 + `Retry-After` | load-shedding CI |
| One of three Kubernetes shards removed | Coordinator remains ready when 2 shards satisfy threshold; search remains HTTP 200 | Kubernetes E2E |
| Two of three Kubernetes shards removed | Coordinator readiness becomes HTTP 503 with `MIN_READY_SHARDS=2` | Kubernetes E2E |
| Removed Kubernetes shards restored | Coordinator returns to ready state | Kubernetes E2E |
| Kafka indexing transient failure | Retry/backoff path is used | Redpanda-backed CI |
| Kafka indexing exhausts retry path | Event can be sent to DLQ | Redpanda-backed CI |
| Coordinator process dies | Orchestrator/load balancer must restart or replace it | Kubernetes primitives configured; multi-replica coordinator HA not claimed |
| Owning shard loses memory but PostgreSQL is configured | Shard can restore documents and rebuild retrieval structures at startup | implementation/tests; no claim of zero-downtime failover |
| Owning shard unavailable without a replica | Search coverage can be incomplete | expected by sharded non-replicated design |

## Partial results are not failover

Nexus currently places a document on one shard. If that shard is unavailable, another shard does not automatically contain the same document.

Therefore:

```text
healthy-shard result continuity != replica failover
```

The coordinator can remain available and return useful results, but recall may drop until the missing shard returns.

## Why readiness can fail while health stays green

A coordinator can be a healthy process while its dependency set is insufficient to safely serve traffic.

Example:

```text
/api/health -> 200
/api/ready  -> 503
```

This means Kubernetes should keep the process alive but remove it from ready endpoints until enough shards recover.

## Overload behavior

The application-level capacity gate is intentionally fail-fast. When all admission slots are occupied, Nexus returns HTTP 503 rather than adding an unbounded application waiter.

This protects process capacity but does not provide:

- a globally coordinated cluster-wide quota;
- fairness between users;
- endpoint-specific distributed rate limits;
- queue prioritization.

Those are separate rate-limiting/backpressure problems.

## Kafka delivery semantics

The indexing worker is designed around at-least-once processing. Duplicate delivery is possible, so correctness depends on idempotent/deduplicated storage behavior rather than claiming exactly-once processing.

## What is not yet claimed

Nexus does not currently claim:

- automatic replica failover;
- zero-downtime shard ownership migration;
- cross-region consensus;
- exactly-once Kafka-to-PostgreSQL processing;
- globally coordinated admission control;
- production-scale corpus capacity;
- a production-managed Prometheus/Grafana/OTel backend.
