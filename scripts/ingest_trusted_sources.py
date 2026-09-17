#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import error, request


def post_json(base_url: str, token: str, body: dict) -> dict:
    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        base_url.rstrip("/") + "/api/crawl",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Admin-Token": token,
        },
    )
    with request.urlopen(req, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Nexus trusted documentation registry.")
    parser.add_argument("--url", default=os.getenv("NEXUS_URL", "https://nexus-search-production.up.railway.app"))
    parser.add_argument("--token", default=os.getenv("ADMIN_TOKEN", ""))
    parser.add_argument("--registry", default="backend/app/data/trusted_sources.json")
    parser.add_argument("--tier", choices=["core", "extended", "all"], default="core")
    parser.add_argument("--source", default="", help="Only ingest a source whose name contains this text.")
    parser.add_argument("--pages", type=int, default=0, help="Override max pages per source.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        print("Missing ADMIN_TOKEN. Export it locally; never paste it into chat.")
        sys.exit(2)

    sources = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    selected = []
    for source in sources:
        if args.tier != "all" and source["tier"] != args.tier:
            continue
        if args.source and args.source.lower() not in source["name"].lower():
            continue
        selected.append(source)

    if not selected:
        print("No sources matched.")
        sys.exit(1)

    print(f"Selected {len(selected)} trusted sources")
    totals = {"crawled": 0, "indexed": 0, "skipped": 0, "failed": 0}

    for idx, source in enumerate(selected, 1):
        pages = args.pages if args.pages > 0 else int(source["max_pages"])
        seeds = source.get("seeds") or [source["seed"]]
        body = {
            "seeds": seeds,
            "max_pages": max(1, min(pages, 100)),
            "max_depth": int(source["max_depth"]),
            "same_domain_only": True,
        }
        print(
            f"\n[{idx}/{len(selected)}] {source['name']} :: "
            f"seeds={len(seeds)} :: pages={body['max_pages']} depth={body['max_depth']}"
        )
        for seed in seeds:
            print(f"  - {seed}")
        if args.dry_run:
            continue
        try:
            result = post_json(args.url, args.token, body)
            print(json.dumps(result, indent=2))
            for key in totals:
                totals[key] += int(result.get(key, 0))
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            print(f"HTTP {exc.code}: {text[:500]}")
            totals["failed"] += 1
        except Exception as exc:
            print(f"ERROR: {exc}")
            totals["failed"] += 1
        time.sleep(0.4)

    if not args.dry_run:
        print("\nTOTALS")
        print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
