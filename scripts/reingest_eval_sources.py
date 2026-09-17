#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys

TARGETS = [
    "Kubernetes Docs",
    "PostgreSQL Docs",
    "Redis Docs",
    "OpenTelemetry Docs",
    "Python Docs",
    "FastAPI Docs",
    "React Docs",
    "Java Docs",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-ingest high-value canonical documentation sources."
    )
    parser.add_argument(
        "--url",
        default=os.getenv(
            "NEXUS_URL",
            "https://nexus-search-production.up.railway.app",
        ),
    )
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.getenv("ADMIN_TOKEN") and not args.dry_run:
        print("Missing ADMIN_TOKEN. Export it locally; never paste it into chat.")
        return 2

    for target in TARGETS:
        cmd = [
            sys.executable,
            "scripts/ingest_trusted_sources.py",
            "--url",
            args.url,
            "--tier",
            "all",
            "--source",
            target,
            "--pages",
            str(args.pages),
        ]
        if args.dry_run:
            cmd.append("--dry-run")
        print("\n$", " ".join(cmd))
        completed = subprocess.run(cmd)
        if completed.returncode != 0:
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
