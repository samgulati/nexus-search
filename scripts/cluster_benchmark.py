from __future__ import annotations

import argparse
import statistics
import time

import httpx

QUERIES = [
    "distributed systems",
    "Kafka partitions consumer groups",
    "hybrid lexical semantic retrieval",
    "transactional outbox",
    "OpenTelemetry distributed tracing",
    "idempotency retries",
    "BM25 inverted index",
    "retrieval augmented generation citations",
]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = int((len(ordered) - 1) * p)
    return ordered[idx]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--rounds", type=int, default=25)
    args = parser.parse_args()

    latencies: list[float] = []
    with httpx.Client(timeout=10) as client:
        for _ in range(args.rounds):
            for query in QUERIES:
                t0 = time.perf_counter()
                response = client.get(
                    f"{args.base_url.rstrip('/')}/api/search",
                    params={"q": query, "mode": "hybrid", "top_k": 10},
                )
                response.raise_for_status()
                latencies.append((time.perf_counter() - t0) * 1000)

    print(f"requests={len(latencies)}")
    print(f"avg_ms={statistics.mean(latencies):.3f}")
    print(f"p50_ms={percentile(latencies, 0.50):.3f}")
    print(f"p95_ms={percentile(latencies, 0.95):.3f}")
    print(f"p99_ms={percentile(latencies, 0.99):.3f}")


if __name__ == "__main__":
    main()
