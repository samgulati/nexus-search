from app.observability import (
    inject_trace_headers,
    render_metrics,
    trace_id_hex,
    tracer,
)


def test_trace_context_injection_contains_w3c_traceparent():
    with tracer().start_as_current_span("test-trace"):
        headers = inject_trace_headers({"X-Test": "1"})
        assert headers["X-Test"] == "1"
        assert "traceparent" in headers
        assert trace_id_hex()


def test_prometheus_payload_exposes_nexus_metrics():
    payload, content_type = render_metrics()
    assert b"nexus_http_requests_total" in payload
    assert "text/plain" in content_type

from app.observability import extract_kafka_context, kafka_trace_headers, tracer


def test_kafka_trace_headers_preserve_trace_id():
    with tracer().start_as_current_span("producer") as producer_span:
        producer_trace_id = producer_span.get_span_context().trace_id
        headers = kafka_trace_headers()

    extracted = extract_kafka_context(headers)
    with tracer().start_as_current_span("consumer", context=extracted) as consumer_span:
        assert consumer_span.get_span_context().trace_id == producer_trace_id
