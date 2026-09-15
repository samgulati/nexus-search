from __future__ import annotations

import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.models import DocumentIn
from app.services.index_service import IndexService

seed_path = ROOT / 'backend' / 'app' / 'data' / 'seed_documents.json'
seed = json.loads(seed_path.read_text())

service = IndexService()
random.seed(42)
items = []
for i in range(1200):
    source = random.choice(seed)
    extra = f" Synthetic benchmark document {i}. node-{i % 17} shard-{i % 11} retry-policy-{i % 7}."
    items.append(DocumentIn(title=f"{source['title']} #{i}", text=source['text'] + extra, url=source.get('url'), source='benchmark'))

start = time.perf_counter()
service.add_many(items)
index_ms = (time.perf_counter() - start) * 1000

queries = [
    'Kafka duplicate processing idempotency',
    'semantic retrieval vector similarity',
    'distributed trace context propagation',
    'database indexing query planning',
    'crawler duplicate detection frontier',
    'retries exponential backoff jitter',
    'hybrid BM25 reciprocal rank fusion',
    'Kubernetes rolling deployment readiness',
] * 15

latencies = []
for q in queries:
    t = time.perf_counter()
    service.search(q, mode='hybrid', top_k=10)
    latencies.append((time.perf_counter() - t) * 1000)

latencies_sorted = sorted(latencies)
result = {
    'documents': len(service.documents),
    'vocabulary_terms': service.index.bm25.vocabulary_size,
    'index_build_ms': round(index_ms, 2),
    'queries': len(latencies),
    'p50_ms': round(statistics.median(latencies), 3),
    'p95_ms': round(latencies_sorted[int((len(latencies_sorted)-1)*0.95)], 3),
    'avg_ms': round(statistics.mean(latencies), 3),
    'max_ms': round(max(latencies), 3),
}
print(json.dumps(result, indent=2))
