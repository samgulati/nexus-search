from app.models import DocumentIn
from app.services.index_service import IndexService


def build_service():
    service = IndexService()
    service.add_many([
        DocumentIn(title="Kafka", text="Kafka partitions provide ordering and consumer groups provide parallel message processing.", source="test"),
        DocumentIn(title="Redis", text="Redis is an in-memory data store commonly used for low latency caching and counters.", source="test"),
        DocumentIn(title="Tracing", text="OpenTelemetry distributed tracing propagates trace context across service boundaries.", source="test"),
        DocumentIn(title="RAG", text="Retrieval augmented generation grounds language model answers in retrieved evidence with citations.", source="test"),
    ])
    return service


def test_bm25_search_finds_exact_topic():
    service = build_service()
    response = service.search("Kafka consumer partitions", mode="lexical", top_k=3)
    assert response.results
    assert response.results[0].title == "Kafka"


def test_semantic_search_returns_related_document():
    service = build_service()
    response = service.search("trace context across service boundaries", mode="semantic", top_k=3)
    titles = [r.title for r in response.results]
    assert "Tracing" in titles


def test_hybrid_search_has_score_breakdown():
    service = build_service()
    response = service.search("ground answers using evidence", mode="hybrid", top_k=3)
    assert response.results
    assert any(r.semantic_score != 0 or r.bm25_score != 0 for r in response.results)


def test_duplicate_documents_are_deduplicated():
    service = IndexService()
    doc = DocumentIn(title="A", text="This is a sufficiently long document body for deduplication testing.", source="test")
    _, created_1 = service.add_document(doc)
    _, created_2 = service.add_document(doc)
    assert created_1 is True
    assert created_2 is False
    assert len(service.documents) == 1
