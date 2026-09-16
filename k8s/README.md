# Kubernetes deployment

The base manifests run the Nexus coordinator plus three independent search shards.

## Local image

Build the application image from the repository root:

```bash
docker build -t nexus-search:local .
```

For kind:

```bash
kind load docker-image nexus-search:local
kubectl apply -k k8s/base
kubectl -n nexus rollout status deployment/nexus-shard-0
kubectl -n nexus rollout status deployment/nexus-shard-1
kubectl -n nexus rollout status deployment/nexus-shard-2
kubectl -n nexus rollout status deployment/nexus-coordinator
kubectl -n nexus port-forward service/nexus-coordinator 8080:80
```

Then verify:

```bash
curl http://127.0.0.1:8080/api/health
curl http://127.0.0.1:8080/api/ready
curl 'http://127.0.0.1:8080/api/search?q=distributed%20search'
```

`/api/health` is the liveness endpoint. `/api/ready` is used for readiness and, on the coordinator, requires the configured minimum number of healthy shards.

The checked-in secret is intentionally a local-development example. Replace it with a real secret-management mechanism for non-local environments.

This first Kubernetes phase intentionally focuses on orchestration of the search tier. PostgreSQL and Kafka/Redpanda remain external dependencies and are not provisioned by these manifests.

## End-to-end validation

With Docker, kind, kubectl, Python and curl installed, run:

```bash
bash scripts/k8s_e2e.sh
```

The test builds the Nexus image, creates an ephemeral kind cluster, deploys the coordinator and three shards, verifies liveness/readiness/search, removes one shard to verify continued readiness and partial-result service, removes a second shard to verify readiness becomes HTTP 503, then restores both shards and verifies recovery.

Set `KEEP_KIND_CLUSTER=1` to leave the cluster running for inspection after the test.
