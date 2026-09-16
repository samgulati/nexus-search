from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Nexus")
    environment: str = os.getenv("ENVIRONMENT", "development")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    answer_model: str = os.getenv("ANSWER_MODEL", "gpt-5-mini")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    semantic_provider: str = os.getenv("SEMANTIC_PROVIDER", "auto")
    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    crawl_user_agent: str = os.getenv("CRAWL_USER_AGENT", "NexusSearchBot/1.0")
    database_url: str = os.getenv("DATABASE_URL", "")

    # Distributed-search configuration. `standalone` preserves the single-node dev mode.
    service_role: str = os.getenv("SERVICE_ROLE", "standalone").lower()
    shard_id: str = os.getenv("SHARD_ID", "0")
    shard_count: int = int(os.getenv("SHARD_COUNT", "1"))
    shard_urls: str = os.getenv("SHARD_URLS", "")
    cluster_token: str = os.getenv("CLUSTER_TOKEN", "")
    shard_timeout_seconds: float = float(os.getenv("SHARD_TIMEOUT_SECONDS", "5"))
    shard_max_concurrency: int = int(os.getenv("SHARD_MAX_CONCURRENCY", "32"))
    circuit_failure_threshold: int = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "3"))
    circuit_recovery_seconds: float = float(os.getenv("CIRCUIT_RECOVERY_SECONDS", "15"))
    max_inflight_requests: int = int(os.getenv("MAX_INFLIGHT_REQUESTS", "64"))
    min_ready_shards: int = int(os.getenv("MIN_READY_SHARDS", "1"))

    # Adaptive Search Autopilot.
    autopilot_enabled: bool = os.getenv("AUTOPILOT_ENABLED", "true").lower() in {"1", "true", "yes"}
    autopilot_latency_budget_ms: float = float(os.getenv("AUTOPILOT_LATENCY_BUDGET_MS", "1000"))
    autopilot_warn_load_ratio: float = float(os.getenv("AUTOPILOT_WARN_LOAD_RATIO", "0.65"))
    autopilot_emergency_load_ratio: float = float(os.getenv("AUTOPILOT_EMERGENCY_LOAD_RATIO", "0.85"))
    autopilot_generation_cutoff_load_ratio: float = float(os.getenv("AUTOPILOT_GENERATION_CUTOFF_LOAD_RATIO", "0.75"))
    autopilot_min_health_ratio: float = float(os.getenv("AUTOPILOT_MIN_HEALTH_RATIO", "0.67"))

    # Phase 3b: asynchronous indexing through Kafka / Redpanda.
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
    kafka_index_topic: str = os.getenv("KAFKA_INDEX_TOPIC", "nexus-index")
    kafka_dlq_topic: str = os.getenv("KAFKA_DLQ_TOPIC", "nexus-index-dlq")
    kafka_consumer_group: str = os.getenv("KAFKA_CONSUMER_GROUP", "nexus-indexers")
    kafka_client_id: str = os.getenv("KAFKA_CLIENT_ID", "nexus-search")
    index_retry_max: int = int(os.getenv("INDEX_RETRY_MAX", "5"))
    index_retry_base_seconds: float = float(os.getenv("INDEX_RETRY_BASE_SECONDS", "0.5"))
    index_retry_max_seconds: float = float(os.getenv("INDEX_RETRY_MAX_SECONDS", "10"))

    # Phase 4: observability.
    metrics_enabled: bool = os.getenv("METRICS_ENABLED", "false").lower() in {"1", "true", "yes"}
    metrics_port: int = int(os.getenv("METRICS_PORT", "0"))
    otel_exporter_otlp_endpoint: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    otel_exporter_otlp_traces_endpoint: str = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
    otel_service_name: str = os.getenv("OTEL_SERVICE_NAME", "")


settings = Settings()
