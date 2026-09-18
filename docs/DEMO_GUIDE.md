# Nexus Demo Guide

## Thesis

Most RAG demos retrieve text and immediately generate. Nexus treats answering as a separate decision: it measures evidence relevance, authority, coverage, support and agreement, can abstain, and adapts retrieval from live system state.

## 2-3 minute demo

### Supported query

```text
What does HTTP 503 Service Unavailable mean?
```

Show the Autopilot plan, ranked evidence, citation-grounded answer and Evidence Inspector.

### Underspecified query

```text
Should I retry?
```

Expected behavior: qualified answer or abstention, never a definitive recommendation.

### Fabricated identifier

```text
What does the Redis PERMASTORE command do?
```

Expected behavior: abstain.

The memorable sequence is:

```text
supported -> answer
underspecified -> abstain for context
fabricated identifier -> abstain
```

## What to point at

- selected retrieval mode and operating tier;
- shard health, load and p95;
- evidence confidence / coverage / relevance / authority;
- kept vs discarded evidence;
- agreement/conflict state;
- deterministic decision reasons.

## Avoid these claims

Do not call the 40-query benchmark generalized accuracy.  
Do not claim semantic contradiction detection; conflict checks are heuristic.  
Do not claim exactly-once ingestion.  
Do not claim shard replication/failover.  
Do not call the current deployment internet scale.


## Candidate retrieval vs accepted evidence

The right-hand panel shows retrieval candidates, not claims Nexus has accepted as
evidence. Candidates used by the final answer are marked `used as evidence`;
other raw retrieval hits are marked `candidate only`.

This is intentional: retrieval optimizes recall, while the evidence layer applies
stricter support and answerability checks.
