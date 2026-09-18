# Nexus Interview Defense Guide

Use this document to explain Nexus accurately in an SDE-2 / backend / distributed-systems interview.

## 1. 30-second summary

Nexus is a distributed, evidence-aware technical search engine I built from first principles. Each shard maintains lexical and semantic retrieval structures, the coordinator fans queries out concurrently and merges rankings with reciprocal-rank fusion, and an evidence layer separately decides whether retrieved support is relevant, authoritative and sufficient enough to answer. I added deterministic abstention/qualification, explainable adaptive retrieval from live system state, PostgreSQL-backed shard recovery, Kafka/Redpanda asynchronous indexing, resilience controls, observability, and Kubernetes failure tests.

## 2. 2-minute architecture explanation

A request first reaches the coordinator. For search, the coordinator sends the query concurrently to three independent shard services. Each shard performs BM25, semantic, or hybrid retrieval over only the documents it owns. The coordinator collects ranked candidate lists from the shards and performs a global RRF merge with deterministic tie-breaking before returning top-k results.

Documents are assigned to exactly one shard through rendezvous hashing. For asynchronous writes, the request is placed onto Kafka/Redpanda, an indexing worker consumes it, calculates the owner shard, then calls the shard's internal indexing endpoint. PostgreSQL can act as durable shard document storage while the actual BM25/semantic structures are rebuilt in memory.

The design is intentionally failure-aware: shard calls have timeouts, independent circuit breakers, bounded fan-out concurrency, and the coordinator can return partial results from surviving shards. Expensive public requests are also protected by a process-local admission gate. Kubernetes uses separate liveness and readiness probes, so the process can remain alive while being removed from traffic if too few shards are healthy.

## 3. Why BM25 and semantic search together?

BM25 is strong for exact lexical intent, rare terms, identifiers, and explicit keyword overlap. Semantic retrieval can find conceptually related documents even when wording differs.

Neither one dominates every query, so Nexus keeps them as separate rankers and combines their ranked outputs.

## 4. Why RRF?

Lexical and semantic scores are not naturally comparable. A BM25 score and a cosine/similarity score have different scales and distributions.

RRF combines rank positions rather than raw scores:

```text
score(document) += 1 / (k + rank)
```

That makes fusion simple and robust without having to calibrate score distributions first.

## 5. Why not compare shard-local BM25 scores directly?

BM25 depends on statistics such as document frequency, corpus size, and average document length. Shards have different local corpus statistics, so their raw scores are not globally calibrated.

Nexus therefore merges ranked lists instead of assuming shard-local scores are directly comparable.

## 6. Why rendezvous hashing?

Modulo hashing (`hash(key) % N`) remaps a large portion of keys when `N` changes.

Rendezvous hashing computes a score for the key against every shard and chooses the highest-scoring shard. When membership changes, only keys whose winning shard changes need to move.

Tradeoff: the current project does not yet implement the online rebalancing workflow required to physically migrate documents after a topology change.

## 7. Is this highly available?

Not fully.

Nexus currently shards documents but does not replicate each shard's data to another serving replica. If one shard disappears, the coordinator can continue returning results from healthy shards, but recall may fall because documents owned by the missing shard are unavailable.

That is graceful degradation, not shard failover.

## 8. Circuit breaker vs timeout vs bounded concurrency

They solve different problems.

- **Timeout** bounds how long one call can occupy resources.
- **Circuit breaker** stops repeatedly calling a dependency that is already known to be failing.
- **Bounded concurrency** limits how many dependency calls can exist simultaneously.

Together they reduce cascading failure risk.

## 9. Why admission control?

Without admission control, a server under overload can accept more work than it can process, increasing queueing, latency, memory consumption, and eventually failure.

Nexus caps in-flight expensive requests per process. If capacity is exhausted, new requests receive HTTP 503 + `Retry-After` instead of entering an unbounded application waiter queue.

Current limitation: this is not a global distributed rate limiter.

## 10. Why separate readiness and liveness?

Liveness answers: "Is this process alive?"

Readiness answers: "Should this process receive traffic right now?"

A coordinator can still be a healthy process while too many shards are unavailable. Killing/restarting the coordinator would not fix that dependency problem. Kubernetes should keep it alive but stop routing traffic to it.

## 11. Kafka delivery semantics

The indexing pipeline is designed around at-least-once processing, not exactly-once.

Consumers can see a message again, so correctness comes from idempotent/deduplicated writes rather than assuming one delivery.

Retries use backoff, and exhausted failures can be routed to a DLQ.

## 12. Why PostgreSQL if the index is in memory?

PostgreSQL stores durable document state. Search structures are optimized in-memory derived state.

On startup a shard can reload its owned documents and deterministically rebuild BM25/semantic indexes.

That avoids treating the in-memory search representation itself as the source of truth.

## 13. What happens if PostgreSQL succeeds but Kafka retry happens again?

A duplicate delivery can occur. Storage/idempotency behavior must make replay safe.

Nexus does not claim a distributed exactly-once transaction spanning Kafka, shard HTTP calls, and PostgreSQL.

## 14. What did the Kubernetes test prove?

The CI-backed `kind` test proved:

```text
3/3 shards -> ready + search works
2/3 shards -> still ready + search works
1/3 shards -> readiness becomes 503
3/3 restored -> readiness recovers
```

It verifies orchestration and dependency-aware readiness under controlled shard removal and restoration.

It does not prove multi-region availability or replicated shard failover.

## 15. What do the benchmarks prove?

The strongest repeated deployed measurement is three 500-request runs across multiple concurrency levels.

At concurrency 60, the median was approximately:

```text
82.45 completed req/s
714.69 ms p50
1.04 s p95
1.51 s p99
100% HTTP 200 in those runs
```

At concurrency 80, throughput dropped while tail latency increased sharply, so concurrency 60 was the clearest observed performance knee.

The benchmark uses a small deployment corpus and Railway infrastructure, so it is a deployment/performance observation, not an internet-scale capacity claim.

## 16. Strongest failure-injection result

The project includes a controlled scenario where:

- shard failures trigger circuit-breaker behavior;
- healthy shard results remain available;
- the open breaker rejects failing-shard work quickly;
- a half-open probe later verifies recovery.

Separately, an overload test uses an in-flight capacity of 2 with 20 concurrent searches and verifies excess requests are shed with HTTP 503.

## 17. What would you build next?

A strong answer is:

1. replica groups per logical shard;
2. health-aware replica routing;
3. a rebalancing protocol for shard membership changes;
4. retrieval-quality evaluation (Recall@K, MRR, nDCG);
5. ANN/HNSW for larger semantic corpora;
6. production identity/mTLS and secret management;
7. externally managed observability dashboards/alerts.

Avoid answering "more features." Focus on production properties.

## 18. Common traps

Do not say:

```text
"Nexus has failover."
"Nexus is exactly-once."
"Nexus supports 82 req/s universally."
"Nexus is internet scale."
"Kubernetes automatically makes the data highly available."
"503s were observed in the Railway benchmark."
```

Say:

```text
"Nexus returns partial results from healthy shards."
"The Kafka pipeline is at-least-once with idempotent handling."
"I observed ~82 req/s median at concurrency 60 in repeated Railway runs."
"The benchmark validates the tested deployment, not universal capacity."
"Kubernetes manages process orchestration; shard data is not replicated today."
"Load shedding was verified in controlled CI failure testing."
```

## 19. Resume bullet set

A concise project version:

**Nexus — Distributed AI Search Engine | Python, FastAPI, Kafka/Redpanda, PostgreSQL, OpenTelemetry, Kubernetes, React**

- Built a 3-shard distributed hybrid search engine with custom BM25, semantic retrieval, RRF cross-shard ranking, rendezvous-hashed placement, and PostgreSQL-backed shard recovery.
- Engineered Kafka/Redpanda asynchronous indexing with idempotent handling, exponential retry and DLQ paths; validated the pipeline end-to-end with real Redpanda in CI.
- Added circuit breaking, bounded fan-out, request admission control and dependency-aware readiness; CI failure tests verify partial-result continuity, overload shedding, shard isolation, and recovery.
- Deployed/tested coordinator + 3 shards on Kubernetes `kind`; repeated Railway benchmarks observed a median ~82 req/s at 60-way concurrency with 100% HTTP 200 across three 500-request runs.

## 20. Interview drill questions

Be able to answer these without looking at the code:

1. Draw the request path for hybrid search.
2. Why does RRF make sense across shards?
3. What data moves if shard count changes?
4. Why is partial-result search not failover?
5. Where is idempotency enforced in async indexing?
6. What happens when a Kafka message is processed twice?
7. How does the circuit breaker transition between states?
8. What problem remains even after adding a circuit breaker?
9. Why can `/api/health` be 200 while `/api/ready` is 503?
10. What happens if all request-gate slots are occupied?
11. What is the current durability source of truth?
12. Why does Kubernetes not automatically give shard HA?
13. What does the p95 benchmark include?
14. Why did throughput get worse at concurrency 80?
15. How would you add shard replicas without duplicating search results?

## 21. What makes Nexus different from ordinary RAG?

A common RAG flow is:

```text
retrieve top-k -> prompt -> generate
```

Nexus adds explicit decision stages:

```text
retrieve
-> relevance reranking
-> exact identifier grounding where needed
-> sentence-level claim support
-> intent-aware answerability
-> authority / coverage / source-diversity checks
-> heuristic agreement/conflict checks
-> answer / qualify / abstain
```

The fake Redis `PERMASTORE` case is the clearest example: Redis persistence documentation is topically related, but it is not evidence that the command exists.

## 22. Quality result to explain

The frozen Phase 12.6 production benchmark contains 40 queries across definitions, procedures, identifiers, judgment/tradeoff prompts, multi-source questions, out-of-domain prompts, ambiguous prompts, adversarial prompts and regressions.

```text
behavioral-policy pass           100.0%
authoritative-domain coverage@20 100.0%
MRR                                0.5357
Recall@5                           0.5000
domain-proxy nDCG@5                0.5081
```

Caveat: ranking metrics use domain-level relevance proxies in v1, and behavioral pass is a policy check rather than factual accuracy.

