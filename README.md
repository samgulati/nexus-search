# Nexus — Distributed AI Search Engine

Nexus is a from-first-principles distributed search engine that combines a custom BM25 inverted index, semantic retrieval, reciprocal-rank fusion (RRF), asynchronous crawling/indexing, PostgreSQL-backed shard state, Kafka/Redpanda indexing, OpenTelemetry/Prometheus-style observability, resilience controls, and citation-grounded answers.

The same application image can run as a standalone node, coordinator, or shard. The distributed topology has been exercised both on Railway and in Kubernetes `kind` failure-recovery tests.

## Architecture

```mermaid
flowchart LR
    U[React client] --> C[Coordinator]

    C -->|fan-out| S0[Shard 0]
    C -->|fan-out| S1[Shard 1]
    C -->|fan-out| S2[Shard 2]

    S0 --> I0[BM25 + semantic]
    S1 --> I1[BM25 + semantic]
    S2 --> I2[BM25 + semantic]

    I0 --> C
    I1 --> C
    I2 --> C
    C --> R[Global RRF merge]
    R --> G[Grounded answer + citations]
    G --> U

    W[Async index API / crawler] --> K[Kafka / Redpanda]
    K --> X[Index worker]
    X --> H[Rendezvous hashing]
    H --> S0
    H --> S1
    H --> S2

    S0 --> P[(PostgreSQL)]
    S1 --> P
    S2 --> P
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for component boundaries, request flows, consistency choices, and scaling tradeoffs.

## Core implementation

- **Custom lexical retrieval** — BM25 with tokenizer, posting lists, document frequency/IDF, length normalization, and ranked retrieval.
- **Semantic retrieval** — OpenAI embeddings when configured, with a local latent-semantic fallback for dependency-light operation.
- **Hybrid search** — reciprocal-rank fusion combines lexical and semantic rankings without mixing incompatible raw score scales.
- **Distributed query fan-out** — the coordinator concurrently queries independently deployed shards and performs global rank fusion.
- **Deterministic document placement** — highest-random-weight / rendezvous hashing maps each document to one shard and minimizes movement when shard membership changes.
- **Durable shard state** — optional PostgreSQL persistence lets a shard restore its owned documents and rebuild in-memory search structures at startup.
- **Asynchronous indexing** — Kafka/Redpanda-backed ingestion with idempotent handling, exponential retry, and dead-letter routing.
- **Failure-aware search** — shard failures do not automatically fail the whole query; surviving shard results can still be returned.
- **Circuit breaking + bounded fan-out** — per-shard protection prevents repeated slow/failing calls from consuming coordinator capacity indefinitely.
- **Admission control** — application-level in-flight request limits shed excess work with HTTP 503 and `Retry-After`.
- **Readiness vs liveness** — `/api/health` checks process liveness; `/api/ready` reflects whether the configured minimum shard availability is met.
- **Observability** — request, shard-call, indexing, retry/DLQ, consumer-lag, and admission-control metrics, plus OpenTelemetry context propagation.
- **Kubernetes orchestration** — coordinator + three shard Deployments, Services, resource requests/limits, ConfigMap/Secret configuration, liveness/readiness probes, and CI-backed `kind` failure tests.
- **Grounded answers** — citation-aware answer synthesis with deterministic extractive fallback when no LLM key is configured.
- **Crawler safety** — robots.txt handling, canonicalization, duplicate-content hashing, depth/page bounds, and SSRF/private-network protections.

## Service roles

```text
SERVICE_ROLE=standalone
SERVICE_ROLE=coordinator
SERVICE_ROLE=shard
```

A three-shard Docker topology can be started with:

```bash
docker compose -f docker-compose.distributed.yml up --build
```

The coordinator is then available at `http://localhost:8000`.

## Distributed configuration

Coordinator:

```text
SERVICE_ROLE=coordinator
SHARD_COUNT=3
SHARD_URLS=0=http://shard-0:8000,1=http://shard-1:8000,2=http://shard-2:8000
CLUSTER_TOKEN=<shared-secret>
MIN_READY_SHARDS=2
MAX_INFLIGHT_REQUESTS=64
```

Shard:

```text
SERVICE_ROLE=shard
SHARD_ID=0
SHARD_COUNT=3
CLUSTER_TOKEN=<shared-secret>
```

Optional durable storage:

```text
DATABASE_URL=postgresql://user:password@host:5432/nexus
```

Optional asynchronous indexing:

```text
KAFKA_BOOTSTRAP_SERVERS=redpanda:9092
KAFKA_INDEX_TOPIC=nexus-index
KAFKA_DLQ_TOPIC=nexus-index-dlq
```

Never commit production values for `CLUSTER_TOKEN`, `ADMIN_TOKEN`, database credentials, or API keys.

## API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/health` | GET | process liveness and service role |
| `/api/ready` | GET | readiness based on role and shard availability |
| `/api/stats` | GET | local or aggregated cluster statistics |
| `/api/search?q=...&mode=hybrid` | GET | distributed lexical / semantic / hybrid search |
| `/api/ask` | POST | grounded answer with citations |
| `/api/index/document` | POST | synchronous document indexing (admin) |
| `/api/index/async` | POST | enqueue asynchronous indexing (admin) |
| `/api/crawl` | POST | crawl and distribute indexed pages (admin) |
| `/metrics` | GET | metrics endpoint when enabled |
| `/docs` | GET | OpenAPI documentation |

Shard-internal `/internal/*` endpoints require `X-Cluster-Token` and are intended for coordinator/worker traffic, not browser clients.

## Testing

Backend/unit tests:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
PYTHONPATH=backend pytest -q backend/tests
```

The CI pipeline additionally exercises:

- real Redpanda indexing paths;
- retry and DLQ behavior;
- circuit-breaker failure injection and half-open recovery;
- overload shedding/readiness behavior;
- deterministic Kubernetes manifest rendering;
- a real `kind` cluster with coordinator/shards and shard-failure recovery.

## Kubernetes

Render the manifests:

```bash
kubectl kustomize k8s/base
```

Run the full ephemeral cluster test:

```bash
bash scripts/k8s_e2e.sh
```

The Kubernetes E2E test verifies:

1. coordinator + three shards become Ready;
2. health/readiness/search work with all three shards;
3. search remains available with one shard removed while readiness stays healthy at 2/3 shards;
4. readiness returns HTTP 503 when only 1/3 shards remains and `MIN_READY_SHARDS=2`;
5. readiness recovers after the removed shards are restored.

See [`k8s/README.md`](k8s/README.md).

## Benchmarks

Nexus deliberately separates algorithm-level and deployment-level measurements.

### Synthetic in-process retrieval

A checked-in synthetic benchmark indexed 1,200 generated documents and measured **0.433 ms p95 in-process hybrid retrieval** across 120 queries. This excludes HTTP, TLS, coordinator fan-out, and network latency.

### Repeated deployed HTTP benchmark

Against the Railway coordinator, three repeated 500-request runs were executed at concurrency 40, 60, 80, and 100. At concurrency 60, the median across the three runs was:

- **82.45 completed requests/s**
- **100% HTTP 200 success**
- **714.69 ms median p50**
- **1,035.38 ms median p95**
- **1,508.70 ms median p99**

At concurrency 80, median throughput fell to **68.58 req/s** while median p95 rose to **2,392.70 ms**, making concurrency 60 the clearest observed latency/throughput knee in these runs.

These numbers are environment-specific deployment observations, not universal capacity claims. See [`BENCHMARK.md`](BENCHMARK.md) for complete scope and caveats.

Reproduce:

```bash
PYTHONPATH=backend python3 scripts/benchmark_http.py \
  --base-url https://nexus-search-production.up.railway.app \
  --requests 500 \
  --concurrency 40,60,80,100 \
  --warmup 10 \
  --output benchmark-results.json
```

## Adaptive Search Autopilot

Nexus can run public search requests with `mode=auto`. The controller chooses an execution plan from live system state rather than using one fixed retrieval path.

Signals currently include:

- query shape (exact/identifier-style vs natural language);
- process in-flight request utilization;
- recent coordinator p95 search latency;
- healthy-shard count;
- per-shard circuit-breaker state.

The controller exposes an explainable plan in the search response, including the selected retrieval mode, operating tier (`quality`, `balanced`, or `survival`), generation decision, latency budget, shard health and decision reasons.

Under healthy conditions, natural-language queries use hybrid retrieval. Exact/identifier-style queries can favor lexical retrieval. Under severe pressure or insufficient shard health, Nexus can fall back to lexical retrieval and disable LLM generation in favor of the grounded extractive answer path.

Explicit `hybrid`, `lexical`, or `semantic` requests remain manual overrides and are never silently replaced by Autopilot.

This feature is a deterministic policy controller, not a learned optimizer; current thresholds are configurable and should be evaluated against workload-specific quality/latency objectives before production use.

## Failure behavior

Nexus has explicit behavior for dependency and overload failures rather than relying on a single generic error path.

See [`docs/FAILURE_MODES.md`](docs/FAILURE_MODES.md) for the tested behavior and the important distinction between graceful partial results and true replica failover.

## Deployment

Public coordinator/UI:

`https://nexus-search-production.up.railway.app`

The Railway deployment uses a public coordinator and private shard services. Kubernetes manifests are provided separately for orchestration and local `kind` validation.

## Interview-ready design decisions

- Why BM25 still matters next to semantic retrieval.
- Why RRF is safer than directly combining lexical and semantic raw scores.
- Why shard-local relevance scores should not be naively summed across machines.
- Rendezvous hashing vs modulo hashing vs a consistent-hash ring.
- Why a coordinator can return partial results without providing high availability.
- Why circuit breakers and bounded fan-out solve different failure modes.
- Why readiness and liveness must be separate signals.
- Why retries require idempotency.
- Why at-least-once Kafka processing is acceptable when storage writes are idempotent.
- Where backpressure should be enforced and why admission control is per-process in the current design.
- Why the current 3-shard topology is sharding, not replication.
- What would be required to add replica groups, rebalancing, and automatic failover.

## Security and interview documentation

- [`docs/SECURITY.md`](docs/SECURITY.md) — trust boundaries, implemented controls, RAG-specific threats, and non-claims.
- [`docs/INTERVIEW_GUIDE.md`](docs/INTERVIEW_GUIDE.md) — architecture defense, tradeoffs, benchmark interpretation, common traps, and interview drills.

## Current boundaries

Nexus is intentionally not presented as an internet-scale search engine. Important current boundaries include:

- the demonstrated Railway corpus is small;
- the current shard topology assigns each document to one shard, so shard loss can reduce result coverage;
- admission control is process-local rather than a global distributed quota;
- Kubernetes manifests orchestrate the search tier; PostgreSQL and Kafka/Redpanda are treated as external dependencies;
- benchmark results vary with deployment region, network path, and Railway resource allocation.

These constraints are documented so benchmark and reliability claims remain reproducible and defensible.

## License

MIT
