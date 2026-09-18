# Nexus — Pitch, Demo, and Interview Package

## 30-second pitch

Nexus is a distributed, evidence-aware search engine for technical knowledge. Instead of the usual `retrieve -> prompt -> generate` flow, Nexus treats answering as a separate reliability decision. It combines custom BM25, semantic retrieval and RRF across three shards, then checks relevance, authority, query coverage, exact identifiers, sentence-level support and cross-source agreement before deciding to answer, qualify, or abstain. It also adapts retrieval from live shard health, load and latency, and exposes those decisions in the UI.

## 2-minute pitch

I built Nexus because most RAG systems treat retrieval as if it were proof. A topically similar chunk can still fail to support the user's exact question.

The retrieval layer is distributed across three shards. Each shard maintains lexical and semantic indexes, the coordinator fans the query out concurrently, and global results are merged with reciprocal-rank fusion so I do not compare incompatible raw BM25 and semantic scores directly.

Documents are assigned with rendezvous hashing, shard state can be restored from PostgreSQL, and asynchronous indexing runs through Kafka/Redpanda with retries, idempotent handling and a DLQ path.

Above retrieval is the part that makes Nexus different: an evidence layer reranks candidates, checks exact technical identifiers, sentence support, definition/procedural intent, source authority and source diversity, then decides between `answer`, `answer_with_caveat`, and `abstain`.

The system is also failure-aware. It has shard timeouts, circuit breakers, bounded fan-out, admission control, readiness/liveness separation and an adaptive search controller that changes retrieval strategy from live load, latency and shard health.

On the current 40-query production benchmark, Nexus recorded 100% behavioral policy compliance, 100% authoritative-domain coverage@20, MRR 0.5357, Recall@5 0.50 and domain-proxy nDCG@5 0.5081. Those are benchmark-specific metrics, not generalized search accuracy.

## 5-minute architecture story

1. **Coordinator + Autopilot** — public queries arrive at the coordinator. In `auto` mode, Nexus considers query profile, in-flight load, recent p95 latency, shard health and circuit pressure before selecting lexical, semantic or hybrid retrieval.
2. **Distributed retrieval** — the coordinator fans out concurrently to three shards with bounded concurrency and timeouts.
3. **Local search** — each shard runs custom BM25, semantic retrieval, or hybrid retrieval.
4. **Global merge** — ranked lists are merged with RRF instead of comparing incompatible raw shard-local scores.
5. **Evidence decision** — Nexus applies relevance pruning, exact identifier grounding, sentence support, intent-aware answerability, authority/coverage/diversity checks and heuristic agreement/conflict detection.
6. **Persistence + indexing** — rendezvous hashing assigns document ownership, PostgreSQL stores durable shard state, and Kafka/Redpanda handles asynchronous indexing with retry/backoff and DLQ behavior.
7. **Reliability** — timeouts, circuit breakers, bounded fan-out, admission control, readiness/liveness separation and Kubernetes failure tests protect the serving path.

Partial results are graceful degradation, not shard failover. The Kafka path is at-least-once with idempotent handling, not exactly-once.

## 2–3 minute hackathon demo

### Scene 1 — supported fact

Query:

```text
What does HTTP 503 Service Unavailable mean?
```

Show the Autopilot plan, authoritative citation, grounded answer and Evidence Inspector decision = `answer`.

### Scene 2 — underspecified decision

Query:

```text
Should I retry?
```

Expected: `abstain`.

Explain that Nexus found related retry material but refuses to make a context-free recommendation without the failure mode, operation semantics and system context.

### Scene 3 — fabricated identifier

Query:

```text
What does the Redis PERMASTORE command do?
```

Expected: `abstain`.

Explain that Redis persistence pages are topically related, but exact identifier grounding prevents nearby documentation from being treated as proof that the command exists.

### Closing line

> Retrieval should maximize recall; a separate evidence layer should decide whether the system has earned the right to answer.

## Three failure stories

### Right domain, wrong passage

Early diagnostics showed strong authoritative-domain coverage but poor usable passages. I traced candidates through every evidence stage, then added canonical-source ingestion and safe URL-level refresh. I improved the corpus instead of weakening global thresholds.

### Fabricated Redis command

`PERMASTORE` originally matched Redis persistence material. I added exact identifier grounding so fabricated commands abstain while legitimate technical identifiers remain answerable.

### Overload

I added process-local admission control, bounded fan-out, shard timeouts and circuit breakers. Excess work is shed with HTTP 503 rather than admitted into an unbounded waiter queue.

## Interview questions

**Why RRF?** BM25 and semantic scores have unrelated scales; RRF combines ranks instead of pretending raw scores are directly comparable.

**Why rendezvous hashing?** It gives deterministic ownership and reduces remapping when shard membership changes compared with modulo hashing.

**Is Nexus highly available?** Not fully. Search degrades gracefully when a shard disappears, but data is not replicated across serving shard replicas.

**Is indexing exactly-once?** No. The design is at-least-once with idempotent handling and retry/DLQ behavior.

**Why PostgreSQL if indexes are in memory?** PostgreSQL stores durable document state; BM25 and semantic structures are derived serving state rebuilt at startup.

**Why not trust top-k retrieval?** Related text is not necessarily evidence for the exact claim.

**Why can health be 200 while readiness is 503?** The process can be alive while dependencies are too degraded to receive traffic safely.

**What next?** Replica groups, health-aware routing, online rebalancing, passage-level human labels, ANN/HNSW at larger scale, and managed identity/observability.

## Resume version

**Nexus — Distributed Evidence-Aware Search Engine | Python, FastAPI, Kafka/Redpanda, PostgreSQL, OpenTelemetry, Kubernetes, React**

- Built a 3-shard hybrid search engine with custom BM25, semantic retrieval, RRF cross-shard ranking, rendezvous-hashed placement and PostgreSQL-backed recovery.
- Engineered Kafka/Redpanda asynchronous indexing with idempotent handling, exponential retry and DLQ paths; validated real broker/shard flows in CI.
- Added circuit breakers, bounded fan-out, admission control, dependency-aware readiness and adaptive retrieval from live shard health/load/latency.
- Built an evidence layer for authority, relevance, exact identifier grounding, claim support, answerability and deterministic abstention; the 40-query v1 production benchmark recorded 100% behavioral-policy pass, MRR 0.536, Recall@5 0.50 and domain-proxy nDCG@5 0.508.

The benchmark bullet must always be described as benchmark-specific, not generalized accuracy.
