from app.cluster import ClusterService, RendezvousHash
from app.models import DocumentIn, SearchResponse, SearchResult


def result(doc_id: str, title: str, shard: str) -> SearchResult:
    return SearchResult(
        id=doc_id,
        title=title,
        url=None,
        snippet="A sufficiently descriptive search result snippet for a distributed search test.",
        score=1.0,
        bm25_score=1.0,
        semantic_score=0.5,
        source="test",
        shard_id=shard,
    )


def test_rendezvous_hash_is_stable():
    ring = RendezvousHash(["0", "1", "2"])
    first = ring.pick("https://example.com/a")
    for _ in range(20):
        assert ring.pick("https://example.com/a") == first


def test_rendezvous_hash_distributes_keys():
    ring = RendezvousHash(["0", "1", "2"])
    assignments = {ring.pick(f"document-{i}") for i in range(200)}
    assert assignments == {"0", "1", "2"}


def test_seed_partition_assigns_exactly_one_shard():
    item = DocumentIn(
        title="Distributed Search",
        text="This document contains enough content to test deterministic shard allocation.",
        url="https://example.com/distributed-search",
        source="test",
    )
    owners = [
        shard for shard in range(3)
        if ClusterService.seed_belongs_here(item, str(shard), 3)
    ]
    assert len(owners) == 1


def test_cluster_merge_uses_rank_not_incomparable_raw_scores():
    shard_0 = SearchResponse(
        query="distributed systems", mode="hybrid", took_ms=1.0, total=2,
        results=[result("a", "A", "0"), result("b", "B", "0")],
    )
    shard_1 = SearchResponse(
        query="distributed systems", mode="hybrid", took_ms=1.0, total=2,
        results=[result("c", "C", "1"), result("d", "D", "1")],
    )
    merged = ClusterService.merge_ranked([shard_0, shard_1], top_k=4, query="distributed systems")
    assert {r.id for r in merged} == {"a", "b", "c", "d"}
    assert merged[0].score == merged[1].score
    assert merged[0].score > merged[2].score


def test_cluster_merge_breaks_rank_ties_by_query_title_relevance():
    shard_0 = SearchResponse(
        query="idempotency in distributed systems", mode="hybrid", took_ms=1.0, total=1,
        results=[result("kafka", "Apache Kafka Distributed Event Streaming", "0")],
    )
    shard_1 = SearchResponse(
        query="idempotency in distributed systems", mode="hybrid", took_ms=1.0, total=1,
        results=[result("idem", "Idempotency in Distributed Systems", "1")],
    )

    merged = ClusterService.merge_ranked(
        [shard_0, shard_1],
        top_k=2,
        query="idempotency in distributed systems",
    )

    assert merged[0].id == "idem"
    assert merged[0].score == merged[1].score  # RRF still remains the primary signal.
