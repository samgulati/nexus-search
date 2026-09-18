# Nexus Architecture — One Page

```mermaid
flowchart LR
    U[React UI] --> A[Adaptive Search Autopilot]
    A --> C[Coordinator]

    C -->|bounded fan-out| S0[Shard 0]
    C -->|bounded fan-out| S1[Shard 1]
    C -->|bounded fan-out| S2[Shard 2]

    S0 --> B0[BM25 + Semantic]
    S1 --> B1[BM25 + Semantic]
    S2 --> B2[BM25 + Semantic]

    B0 --> R[Global RRF]
    B1 --> R
    B2 --> R

    R --> E[Evidence Layer]
    E -->|answer| G[Grounded Answer]
    E -->|partial| Q[Answer with Caveat]
    E -->|insufficient| X[Abstain]

    W[Crawler / Async Index API] --> K[Kafka / Redpanda]
    K --> IW[Index Worker]
    IW --> H[Rendezvous Hashing]
    H --> S0
    H --> S1
    H --> S2

    S0 --> P[(PostgreSQL)]
    S1 --> P
    S2 --> P
```

## Read path

`query -> adaptive plan -> coordinator -> concurrent shard search -> local BM25/semantic/hybrid -> global RRF -> evidence checks -> answer/caveat/abstain`

## Write path

`crawl/index -> Kafka/Redpanda -> worker -> rendezvous owner -> shard -> PostgreSQL durable state -> serving index`

## Evidence path

`raw candidates -> relevance -> exact identifier -> sentence support -> answerability -> authority/coverage/diversity -> agreement/conflict -> final decision`

## Reliability controls

`timeouts + circuit breakers + bounded fan-out + admission control + readiness/liveness + partial-result search + Kubernetes failure tests`

## Current non-claims

`not internet scale · not exactly-once · not replicated shard failover · not calibrated factual accuracy · not semantic/NLI contradiction detection`
