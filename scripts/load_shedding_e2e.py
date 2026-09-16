from __future__ import annotations

import concurrent.futures
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

CLUSTER_TOKEN = "ci-load-token"
SHARD0_PORT = 19400
SHARD1_PORT = 19401
COORDINATOR_PORT = 19410


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
        time.sleep(0.45)
        self.send_response(503)
        self.end_headers()
        self.wfile.write(b"injected slow shard")

    def log_message(self, fmt, *args):
        return


def wait_http(url: str, expected=(200,), timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code in expected:
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
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    managed = ManagedProcess(process, f"shard-{shard_id}")
    wait_http(f"http://127.0.0.1:{port}/api/health")
    return managed


def start_coordinator() -> ManagedProcess:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": "backend",
        "SERVICE_ROLE": "coordinator",
        "SHARD_URLS": f"0=http://127.0.0.1:{SHARD0_PORT},1=http://127.0.0.1:{SHARD1_PORT}",
        "CLUSTER_TOKEN": CLUSTER_TOKEN,
        "SEMANTIC_PROVIDER": "local",
        "SHARD_TIMEOUT_SECONDS": "0.35",
        "CIRCUIT_FAILURE_THRESHOLD": "100",
        "CIRCUIT_RECOVERY_SECONDS": "1",
        "MAX_INFLIGHT_REQUESTS": "2",
        "MIN_READY_SHARDS": "2",
        "METRICS_ENABLED": "true",
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", str(COORDINATOR_PORT)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    managed = ManagedProcess(process, "coordinator")
    wait_http(f"http://127.0.0.1:{COORDINATOR_PORT}/api/health")
    return managed


def request_search():
    started = time.perf_counter()
    response = httpx.get(
        f"http://127.0.0.1:{COORDINATOR_PORT}/api/search",
        params={"q": "kafka", "mode": "lexical", "top_k": 5},
        timeout=3,
    )
    return response.status_code, time.perf_counter() - started, response.headers.get("Retry-After")


def metrics() -> str:
    response = httpx.get(f"http://127.0.0.1:{COORDINATOR_PORT}/metrics", timeout=3)
    response.raise_for_status()
    return response.text


def main() -> None:
    shard0 = shard1 = coordinator = None
    slow_server = None
    slow_thread = None
    try:
        shard0 = start_shard(0, SHARD0_PORT)
        coordinator = start_coordinator()

        health = httpx.get(f"http://127.0.0.1:{COORDINATOR_PORT}/api/health", timeout=3)
        assert health.status_code == 200

        not_ready = httpx.get(f"http://127.0.0.1:{COORDINATOR_PORT}/api/ready", timeout=3)
        assert not_ready.status_code == 503, not_ready.text
        assert not_ready.json()["healthy_shards"] == 1
        print("READINESS_DEGRADED_OK health=200 ready=503 healthy_shards=1")

        shard1 = start_shard(1, SHARD1_PORT)
        ready = httpx.get(f"http://127.0.0.1:{COORDINATOR_PORT}/api/ready", timeout=3)
        assert ready.status_code == 200, ready.text
        assert ready.json()["healthy_shards"] == 2
        print("READINESS_RECOVERY_OK ready=200 healthy_shards=2")

        shard1.stop()
        shard1 = None

        slow_server = ThreadingHTTPServer(("127.0.0.1", SHARD1_PORT), SlowShardHandler)
        slow_thread = threading.Thread(target=slow_server.serve_forever, daemon=True)
        slow_thread.start()
        time.sleep(0.1)

        request_count = 20
        with concurrent.futures.ThreadPoolExecutor(max_workers=request_count) as pool:
            results = [f.result(timeout=5) for f in [pool.submit(request_search) for _ in range(request_count)]]

        admitted = [(s, e) for s, e, _ in results if s == 200]
        rejected = [(s, e, r) for s, e, r in results if s == 503]

        assert admitted, results
        assert rejected, results
        assert len(admitted) <= 2, results
        assert len(rejected) >= request_count - 2, results
        assert all(retry == "1" for _, _, retry in rejected), rejected

        rejected_times = sorted(e for _, e, _ in rejected)
        rejected_p95 = rejected_times[max(0, int(len(rejected_times) * 0.95) - 1)]
        admitted_min = min(e for _, e in admitted)
        assert rejected_p95 < admitted_min, (rejected_p95, admitted_min)

        m = metrics()
        assert 'nexus_request_gate_events_total{outcome="rejected"}' in m
        assert 'nexus_request_gate_events_total{outcome="admitted"}' in m

        print(
            f"LOAD_SHEDDING_OK requests={request_count} admitted={len(admitted)} "
            f"rejected={len(rejected)} rejected_p95_ms={rejected_p95*1000:.1f} "
            f"admitted_min_ms={admitted_min*1000:.1f}"
        )
        print("PRODUCTION_READINESS_E2E_OK")
    finally:
        if slow_server is not None:
            try:
                slow_server.shutdown()
                slow_server.server_close()
            except Exception:
                pass
        if slow_thread is not None:
            slow_thread.join(timeout=2)
        if coordinator:
            coordinator.stop()
        if shard1:
            shard1.stop()
        if shard0:
            shard0.stop()


if __name__ == "__main__":
    main()
