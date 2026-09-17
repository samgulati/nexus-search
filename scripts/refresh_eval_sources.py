#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib import error, request

TARGETS = {
    "Kubernetes Docs",
    "PostgreSQL Docs",
    "Redis Docs",
    "OpenTelemetry Docs",
    "Python Docs",
    "FastAPI Docs",
    "React Docs",
    "Java Docs",
}


def post_json(base_url: str, token: str, body: dict, timeout: int) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        base_url.rstrip("/") + "/api/crawl",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "X-Admin-Token": token},
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh evaluation-critical canonical Nexus documentation URLs."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("NEXUS_URL", "https://nexus-search-production.up.railway.app"),
    )
    parser.add_argument("--token", default=os.getenv("ADMIN_TOKEN", ""))
    parser.add_argument("--registry", default="backend/app/data/trusted_sources.json")
    parser.add_argument("--source", default="")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        print("Missing ADMIN_TOKEN. Export it locally; never paste it into chat.")
        return 2

    sources = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    selected = [
        source for source in sources
        if source["name"] in TARGETS
        and (not args.source or args.source.lower() in source["name"].lower())
    ]
    if not selected:
        print("No sources matched.")
        return 1

    totals = {
        "crawled": 0,
        "indexed": 0,
        "skipped": 0,
        "failed": 0,
        "refreshed_urls": 0,
        "unchanged_urls": 0,
        "stale_chunks_removed": 0,
    }

    for source in selected:
        seeds = source.get("seeds") or [source["seed"]]
        body = {
            "seeds": seeds,
            "max_pages": len(seeds),
            "max_depth": 0,
            "same_domain_only": True,
            "refresh_existing": True,
        }
        print(f"\n{source['name']} :: refreshing {len(seeds)} canonical URL(s)")
        for seed in seeds:
            print(f"  - {seed}")
        if args.dry_run:
            continue

        try:
            result = post_json(args.url, args.token, body, args.timeout)
            print(json.dumps(result, indent=2))
            for key in totals:
                totals[key] += int(result.get(key, 0))
        except error.HTTPError as exc:
            print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
            totals["failed"] += 1
            print(json.dumps(totals, indent=2))
            return 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            totals["failed"] += 1
            print(json.dumps(totals, indent=2))
            return 1

    if not args.dry_run:
        print("\nTOTALS")
        print(json.dumps(totals, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
