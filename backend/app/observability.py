from __future__ import annotations

import logging
from collections.abc import Iterable

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

from .config import settings

logger = logging.getLogger("nexus.observability")

HTTP_REQUESTS = Counter(
    "nexus_http_requests_total",
    "HTTP requests handled by Nexus.",
    ("role", "method", "path", "status"),
)
HTTP_LATENCY = Histogram(
    "nexus_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("role", "method", "path"),
)
SHARD_CALLS = Counter(
    "nexus_shard_calls_total",
    "Calls to Nexus search shards.",
    ("operation", "shard_id", "outcome"),
)
SHARD_CALL_LATENCY = Histogram(
    "nexus_shard_call_duration_seconds",
    "Latency of calls to Nexus search shards.",
    ("operation", "shard_id"),
)
INDEX_EVENTS = Counter(
    "nexus_index_events_total",
    "Asynchronous indexing events by outcome.",
    ("outcome",),
)
INDEX_PROCESSING = Histogram(
    "nexus_index_event_duration_seconds",
    "Index-worker processing duration in seconds.",
)
KAFKA_LAG = Gauge(
    "nexus_kafka_consumer_lag",
    "Approximate Kafka consumer lag per topic partition.",
    ("topic", "partition"),
)
CIRCUIT_EVENTS = Counter(
    "nexus_circuit_breaker_events_total",
    "Circuit-breaker events by shard and outcome.",
    ("shard_id", "event"),
)
SHARD_INFLIGHT = Gauge(
    "nexus_shard_inflight_requests",
    "Current coordinator requests executing against shards.",
)
REQUEST_GATE_EVENTS = Counter(
    "nexus_request_gate_events_total",
    "Application request-admission outcomes.",
    ("outcome",),
)
APP_INFLIGHT = Gauge(
    "nexus_app_inflight_requests",
    "Current admitted expensive API requests.",
)
AUTOPILOT_DECISIONS = Counter(
    "nexus_autopilot_decisions_total",
    "Adaptive search-plan decisions by tier, retrieval mode, and query profile.",
    ("tier", "selected_mode", "query_profile"),
)

_tracing_configured = False
_worker_metrics_started = False


def _trace_endpoint() -> str:
    explicit = settings.otel_exporter_otlp_traces_endpoint.strip()
    if explicit:
        return explicit
    base = settings.otel_exporter_otlp_endpoint.strip().rstrip("/")
    return f"{base}/v1/traces" if base else ""


def configure_tracing() -> None:
    global _tracing_configured
    if _tracing_configured:
        return

    service_name = (
        settings.otel_service_name.strip()
        or f"{settings.app_name.lower().replace(' ', '-')}-{settings.service_role}"
    )
    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": settings.environment,
            "nexus.role": settings.service_role,
            "nexus.shard_id": settings.shard_id if settings.service_role == "shard" else "",
        }
    )
    provider = TracerProvider(resource=resource)

    endpoint = _trace_endpoint()
    if endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        logger.info("OTLP trace export enabled endpoint=%s", endpoint)

    trace.set_tracer_provider(provider)
    _tracing_configured = True


def tracer():
    return trace.get_tracer("nexus")


def trace_id_hex() -> str:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return ""
    return f"{context.trace_id:032x}"


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    carrier = dict(headers or {})
    propagate.inject(carrier)
    return carrier


def extract_http_context(headers):
    return propagate.extract(dict(headers))


def kafka_trace_headers() -> list[tuple[str, bytes]]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return [(key, value.encode("utf-8")) for key, value in carrier.items()]


def extract_kafka_context(headers: Iterable[tuple[str, bytes]] | None):
    carrier: dict[str, str] = {}
    for key, value in headers or []:
        try:
            carrier[key] = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return propagate.extract(carrier)


def observe_http(method: str, path: str, status: int, duration_seconds: float) -> None:
    HTTP_REQUESTS.labels(settings.service_role, method, path, str(status)).inc()
    HTTP_LATENCY.labels(settings.service_role, method, path).observe(duration_seconds)


def observe_shard_call(
    operation: str,
    shard_id: str,
    outcome: str,
    duration_seconds: float,
) -> None:
    SHARD_CALLS.labels(operation, shard_id, outcome).inc()
    SHARD_CALL_LATENCY.labels(operation, shard_id).observe(duration_seconds)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def start_worker_metrics_server() -> None:
    global _worker_metrics_started
    if _worker_metrics_started or settings.metrics_port <= 0:
        return
    start_http_server(settings.metrics_port)
    _worker_metrics_started = True
    logger.info("worker Prometheus metrics listening port=%s", settings.metrics_port)


configure_tracing()
