from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

CLUSTER_TOKEN = "ci-resilience-token"
SHARD0_PORT = 19300
SHARD1_PORT = 19301
COORDINATOR_PORT = 19310


@dataclass
class ManagedProcess:
    process: subprocess.Popen
    name: str

    def stop(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


class SlowShardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(1.0)
        self.send_response(503)
        self.end_headers()
        self.wfile.write(b"slow injected shard failure")

    def log_message(self, fmt, *args):
        return


def wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {url}")


def start_shard(shard_id: int, port: int) -> ManagedProcess:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": "backend",
        "SERVICE_ROLE": "shard",
        "SHARD_ID": str(shard_id),
        "SHARD_COUNT": "2",
        "CLUSTER_TOKEN": CLUSTER_TOKEN,
        "SEMANTIC_PROVIDER": "local",
        "DATABASE_URL": "",
        "METRICS_ENABLED": "true",
    })
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    m = ManagedProcess(p, f"shard-{shard_id}")
    wait_http(f"http://127.0.0.1:{port}/api/health")
    return m


def start_coordinator() -> ManagedProcess:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": "backend",
        "SERVICE_ROLE": "coordinator",
        "SHARD_URLS": f"0=http://127.0.0.1:{SHARD0_PORT},1=http://127.0.0.1:{SHARD1_PORT}",
        "CLUSTER_TOKEN": CLUSTER_TOKEN,
        "SEMANTIC_PROVIDER": "local",
        "SHARD_TIMEOUT_SECONDS": "0.20",
        "SHARD_MAX_CONCURRENCY": "8",
        "CIRCUIT_FAILURE_THRESHOLD": "2",
        "CIRCUIT_RECOVERY_SECONDS": "0.50",
        "METRICS_ENABLED": "true",
    })
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(COORDINATOR_PORT)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    m = ManagedProcess(p, "coordinator")
    wait_http(f"http://127.0.0.1:{COORDINATOR_PORT}/api/health")
    return m


def query_for_shard0() -> str:
    from app.cluster import ClusterService
    from app.models import DocumentIn
    items = [DocumentIn(**x) for x in json.loads(
        Path("backend/app/data/seed_documents.json").read_text(encoding="utf-8")
    )]
    selected = [x for x in items if ClusterService.seed_belongs_here(x, "0", 2)]
    if not selected:
        raise RuntimeError("No shard-0 seed document found")
    words = [w.strip(".,:;!?()[]{}").lower() for w in selected[0].title.split()]
    useful = [w for w in words if len(w) >= 4]
    return " ".join(useful[:2]) or selected[0].title


def do_search(q: str):
    t0 = time.perf_counter()
    r = httpx.get(
        f"http://127.0.0.1:{COORDINATOR_PORT}/api/search",
        params={"q": q, "mode": "lexical", "top_k": 10},
        timeout=3,
    )
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    return elapsed, r.json()


def get_metrics() -> str:
    r = httpx.get(f"http://127.0.0.1:{COORDINATOR_PORT}/metrics", timeout=3)
    r.raise_for_status()
    return r.text


def main() -> None:
    shard0 = coordinator = shard1 = None
    slow = ThreadingHTTPServer(("127.0.0.1", SHARD1_PORT), SlowShardHandler)
    thread = threading.Thread(target=slow.serve_forever, daemon=True)
    thread.start()
    try:
        shard0 = start_shard(0, SHARD0_PORT)
        coordinator = start_coordinator()
        q = query_for_shard0()

        first_s, first = do_search(q)
        second_s, second = do_search(q)
        third_s, third = do_search(q)

        assert first["results"]
        assert second["results"]
        assert third["results"]

        assert first_s >= 0.15, first_s
        assert second_s >= 0.15, second_s
        assert third_s < 0.12, third_s

        m = get_metrics()
        assert 'nexus_circuit_breaker_events_total{event="failure",shard_id="1"} 2.0' in m
        assert 'nexus_circuit_breaker_events_total{event="rejected",shard_id="1"}' in m

        print(f"CIRCUIT_OPEN_OK first_ms={first_s*1000:.1f} second_ms={second_s*1000:.1f} fail_fast_ms={third_s*1000:.1f}")
        print(f"PARTIAL_RESULTS_OK results={len(third['results'])}")

        slow.shutdown()
        slow.server_close()
        thread.join(timeout=2)

        shard1 = start_shard(1, SHARD1_PORT)
        time.sleep(0.60)

        recovered_s, recovered = do_search(q)
        assert recovered["results"]

        stats = httpx.get(f"http://127.0.0.1:{COORDINATOR_PORT}/api/stats", timeout=3)
        stats.raise_for_status()
        assert stats.json()["healthy_shards"] == 2, stats.text

        m2 = get_metrics()
        assert 'nexus_circuit_breaker_events_total{event="success",shard_id="1"}' in m2

        print(f"HALF_OPEN_RECOVERY_OK recovery_ms={recovered_s*1000:.1f} healthy_shards=2")
        print("RESILIENCE_E2E_OK")
    finally:
        try:
            slow.shutdown()
            slow.server_close()
        except Exception:
            pass
        if shard1:
            shard1.stop()
        if coordinator:
            coordinator.stop()
        if shard0:
            shard0.stop()


if __name__ == "__main__":
    main()
