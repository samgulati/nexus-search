from app.models import DocumentIn, IndexEvent
from app.queueing import event_id_for_document
from app.worker import retry_delay


def test_event_id_is_deterministic_for_normalized_content():
    a = DocumentIn(
        title="A",
        text="Nexus   durable indexing\nwith Kafka and idempotent consumers.",
    )
    b = DocumentIn(
        title="B",
        text="nexus durable indexing with kafka and idempotent consumers.",
    )
    assert event_id_for_document(a) == event_id_for_document(b)


def test_event_id_changes_when_content_changes():
    a = DocumentIn(title="A", text="A sufficiently long first document body.")
    b = DocumentIn(title="A", text="A sufficiently long second document body.")
    assert event_id_for_document(a) != event_id_for_document(b)


def test_index_event_round_trip():
    event = IndexEvent(
        event_id="abc",
        document=DocumentIn(
            title="Doc",
            text="This document body is long enough for validation.",
        ),
    )
    restored = IndexEvent.model_validate_json(event.model_dump_json())
    assert restored == event


def test_retry_delay_is_capped():
    assert retry_delay(1) >= 0
    assert retry_delay(100) <= 10
