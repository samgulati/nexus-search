#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${NEXUS_KIND_CLUSTER:-nexus-e2e}"
IMAGE="${NEXUS_IMAGE:-nexus-search:local}"
PORT="${NEXUS_E2E_PORT:-18080}"
KEEP_CLUSTER="${KEEP_KIND_CLUSTER:-0}"
PF_PID=""

cleanup() {
  if [[ -n "${PF_PID}" ]]; then
    kill "${PF_PID}" >/dev/null 2>&1 || true
    wait "${PF_PID}" >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_CLUSTER}" != "1" ]]; then
    kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for cmd in docker kind kubectl python3 curl; do
  command -v "${cmd}" >/dev/null 2>&1 || {
    echo "missing required command: ${cmd}" >&2
    exit 1
  }
done

echo "==> Building Nexus image"
docker build -t "${IMAGE}" .

echo "==> Creating kind cluster ${CLUSTER_NAME}"
kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
kind create cluster --name "${CLUSTER_NAME}" --wait 120s

echo "==> Loading image into kind"
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"

echo "==> Applying Nexus manifests"
kubectl apply -k k8s/base

for deployment in nexus-shard-0 nexus-shard-1 nexus-shard-2 nexus-coordinator; do
  echo "==> Waiting for ${deployment}"
  kubectl -n nexus rollout status "deployment/${deployment}" --timeout=180s
done

echo "==> Starting coordinator port-forward"
kubectl -n nexus port-forward service/nexus-coordinator "${PORT}:80" >/tmp/nexus-port-forward.log 2>&1 &
PF_PID=$!

BASE="http://127.0.0.1:${PORT}"

wait_http() {
  local url="$1"
  local expected="$2"
  for _ in $(seq 1 60); do
    status="$(curl -sS -o /tmp/nexus-http-body -w '%{http_code}' "${url}" || true)"
    if [[ "${status}" == "${expected}" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "timed out waiting for ${url}; expected ${expected}" >&2
  cat /tmp/nexus-port-forward.log >&2 || true
  cat /tmp/nexus-http-body >&2 || true
  return 1
}

json_assert() {
  local expression="$1"
  python3 -c '
import json, sys
expr = sys.argv[1]
data = json.load(sys.stdin)
if not eval(expr, {"__builtins__": {}}, {"data": data}):
    raise SystemExit(f"assertion failed: {expr}; data={data!r}")
' "${expression}"
}

echo "==> Verifying healthy cluster"
wait_http "${BASE}/api/health" 200
health="$(curl -fsS "${BASE}/api/health")"
printf '%s' "${health}" | json_assert 'data.get("status") == "ok"'

wait_http "${BASE}/api/ready" 200
ready="$(curl -fsS "${BASE}/api/ready")"
printf '%s' "${ready}" | json_assert 'data.get("status") == "ready" and data.get("healthy_shards") == 3'

search_status="$(curl -sS -o /tmp/nexus-search.json -w '%{http_code}' \
  "${BASE}/api/search?q=distributed%20search&mode=hybrid&top_k=10")"
[[ "${search_status}" == "200" ]] || {
  echo "expected initial search 200, got ${search_status}" >&2
  cat /tmp/nexus-search.json >&2
  exit 1
}
echo "K8S_HEALTHY_OK health=200 ready=200 healthy_shards=3 search=200"

echo "==> Removing one shard; coordinator should remain ready"
kubectl -n nexus scale deployment/nexus-shard-2 --replicas=0
kubectl -n nexus wait --for=delete pod -l app=nexus-shard-2 --timeout=120s || true

for _ in $(seq 1 30); do
  status="$(curl -sS -o /tmp/nexus-ready-one-down.json -w '%{http_code}' "${BASE}/api/ready" || true)"
  if [[ "${status}" == "200" ]] && python3 - <<'PY'
import json
with open("/tmp/nexus-ready-one-down.json") as f:
    d=json.load(f)
raise SystemExit(0 if d.get("healthy_shards") == 2 else 1)
PY
  then
    break
  fi
  sleep 1
done
python3 - <<'PY'
import json
with open("/tmp/nexus-ready-one-down.json") as f:
    d=json.load(f)
assert d.get("status") == "ready", d
assert d.get("healthy_shards") == 2, d
PY

search_status="$(curl -sS -o /tmp/nexus-search-degraded.json -w '%{http_code}' \
  "${BASE}/api/search?q=distributed%20search&mode=hybrid&top_k=10")"
[[ "${search_status}" == "200" ]] || {
  echo "expected degraded search 200, got ${search_status}" >&2
  cat /tmp/nexus-search-degraded.json >&2
  exit 1
}
echo "K8S_ONE_SHARD_DOWN_OK ready=200 healthy_shards=2 search=200"

echo "==> Removing second shard; coordinator should become unready"
kubectl -n nexus scale deployment/nexus-shard-1 --replicas=0
kubectl -n nexus wait --for=delete pod -l app=nexus-shard-1 --timeout=120s || true

for _ in $(seq 1 30); do
  status="$(curl -sS -o /tmp/nexus-ready-two-down.json -w '%{http_code}' "${BASE}/api/ready" || true)"
  if [[ "${status}" == "503" ]]; then
    break
  fi
  sleep 1
done
[[ "${status}" == "503" ]] || {
  echo "expected readiness 503 with two shards down, got ${status}" >&2
  cat /tmp/nexus-ready-two-down.json >&2 || true
  exit 1
}
python3 - <<'PY'
import json
with open("/tmp/nexus-ready-two-down.json") as f:
    d=json.load(f)
assert d.get("status") == "not_ready", d
assert d.get("healthy_shards") == 1, d
assert d.get("required_shards") == 2, d
PY
echo "K8S_READINESS_DEGRADED_OK ready=503 healthy_shards=1 required_shards=2"

echo "==> Restoring shards"
kubectl -n nexus scale deployment/nexus-shard-1 --replicas=1
kubectl -n nexus scale deployment/nexus-shard-2 --replicas=1
kubectl -n nexus rollout status deployment/nexus-shard-1 --timeout=180s
kubectl -n nexus rollout status deployment/nexus-shard-2 --timeout=180s

for _ in $(seq 1 40); do
  status="$(curl -sS -o /tmp/nexus-ready-recovered.json -w '%{http_code}' "${BASE}/api/ready" || true)"
  if [[ "${status}" == "200" ]] && python3 - <<'PY'
import json
with open("/tmp/nexus-ready-recovered.json") as f:
    d=json.load(f)
raise SystemExit(0 if d.get("healthy_shards") == 3 else 1)
PY
  then
    break
  fi
  sleep 1
done
python3 - <<'PY'
import json
with open("/tmp/nexus-ready-recovered.json") as f:
    d=json.load(f)
assert d.get("status") == "ready", d
assert d.get("healthy_shards") == 3, d
PY

echo "K8S_RECOVERY_OK ready=200 healthy_shards=3"
echo "K8S_E2E_OK"
