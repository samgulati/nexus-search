from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx


@dataclass
class BenchmarkResult:
    concurrency: int
    requests: int
    duration_seconds: float
    throughput_rps: float
    success_count: int
    rejected_count: int
    error_count: int
    success_rate: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    avg_ms: float
    max_ms: float


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


async def run_level(
    base_url: str,
    query: str,
    concurrency: int,
    requests: int,
    timeout: float,
) -> BenchmarkResult:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    success = rejected = errors = 0

    limits = httpx.Limits(
        max_connections=max(concurrency * 2, 20),
        max_keepalive_connections=max(concurrency, 10),
    )

    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async def one() -> tuple[int | None, float]:
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.get(
                        f"{base_url.rstrip('/')}/api/search",
                        params={"q": query, "mode": "hybrid", "top_k": 10},
                    )
                    return response.status_code, (time.perf_counter() - started) * 1000
                except Exception:
                    return None, (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        outcomes = await asyncio.gather(*(one() for _ in range(requests)))
        duration = time.perf_counter() - started

    for status, latency in outcomes:
        latencies.append(latency)
        if status == 200:
            success += 1
        elif status == 503:
            rejected += 1
        else:
            errors += 1

    return BenchmarkResult(
        concurrency=concurrency,
        requests=requests,
        duration_seconds=round(duration, 4),
        throughput_rps=round(requests / duration, 2) if duration else 0.0,
        success_count=success,
        rejected_count=rejected,
        error_count=errors,
        success_rate=round(success / requests * 100, 2) if requests else 0.0,
        p50_ms=round(percentile(latencies, 0.50), 2),
        p95_ms=round(percentile(latencies, 0.95), 2),
        p99_ms=round(percentile(latencies, 0.99), 2),
        avg_ms=round(statistics.fmean(latencies), 2) if latencies else 0.0,
        max_ms=round(max(latencies), 2) if latencies else 0.0,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reproducible Nexus HTTP benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--query", default="distributed search")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", default="1,5,10,20,40")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--output", default="benchmark-results.json")
    args = parser.parse_args()

    levels = [int(x.strip()) for x in args.concurrency.split(",") if x.strip()]
    if not levels or any(x <= 0 for x in levels):
        raise SystemExit("Concurrency levels must be positive integers")

    health_url = f"{args.base_url.rstrip('/')}/api/health"
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        health = await client.get(health_url)
        health.raise_for_status()

        for _ in range(max(0, args.warmup)):
            response = await client.get(
                f"{args.base_url.rstrip('/')}/api/search",
                params={"q": args.query, "mode": "hybrid", "top_k": 10},
            )
            if response.status_code not in {200, 503}:
                response.raise_for_status()

    results: list[BenchmarkResult] = []
    for concurrency in levels:
        result = await run_level(
            args.base_url,
            args.query,
            concurrency,
            args.requests,
            args.timeout,
        )
        results.append(result)
        print(
            f"concurrency={result.concurrency:>3} "
            f"rps={result.throughput_rps:>8.2f} "
            f"p50={result.p50_ms:>8.2f}ms "
            f"p95={result.p95_ms:>8.2f}ms "
            f"p99={result.p99_ms:>8.2f}ms "
            f"200={result.success_count:>4} "
            f"503={result.rejected_count:>4} "
            f"errors={result.error_count:>3}"
        )

    report = {
        "benchmark": "nexus-http-search",
        "base_url": args.base_url,
        "query": args.query,
        "requests_per_level": args.requests,
        "warmup_requests": args.warmup,
        "concurrency_levels": levels,
        "generated_unix_seconds": time.time(),
        "results": [asdict(r) for r in results],
        "notes": [
            "Throughput is total completed HTTP requests divided by wall-clock duration.",
            "Latency includes client-observed network and server time.",
            "HTTP 503 responses are tracked separately as deliberate load shedding.",
            "Results are environment-specific and should not be generalized beyond the recorded run.",
        ],
    }

    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    asyncio.run(main())
