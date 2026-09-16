from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.adaptive import AdaptiveSearchController


class FakeCluster:
    def __init__(self, *, healthy=3, total=3, p95=100.0, circuits=None):
        self.is_coordinator = total > 1
        self.shard_targets = [SimpleNamespace(shard_id=str(i)) for i in range(total)]
        self.last_healthy_shards = healthy
        self._p95 = p95
        self._circuits = circuits or {"closed": total, "open": 0, "half_open": 0}

    def observed_p95_ms(self):
        return self._p95

    async def circuit_state_counts(self):
        return dict(self._circuits)


def make_plan(query, *, requested="auto", inflight=1, capacity=64, cluster=None):
    controller = AdaptiveSearchController()
    return asyncio.run(
        controller.plan(
            query=query,
            requested_mode=requested,
            inflight=inflight,
            capacity=capacity,
            cluster_service=cluster or FakeCluster(),
        )
    )


def test_healthy_natural_language_uses_quality_hybrid():
    result = make_plan("why combine lexical and semantic retrieval")
    assert result.tier == "quality"
    assert result.selected_mode == "hybrid"
    assert result.generation_allowed is True


def test_exact_identifier_query_prefers_lexical():
    result = make_plan("HTTP_503")
    assert result.tier == "quality"
    assert result.query_profile == "exact"
    assert result.selected_mode == "lexical"


def test_high_process_pressure_enters_survival_mode():
    result = make_plan("explain distributed consensus tradeoffs", inflight=9, capacity=10)
    assert result.tier == "survival"
    assert result.selected_mode == "lexical"
    assert result.generation_allowed is False


def test_low_shard_health_enters_survival_mode():
    result = make_plan(
        "explain distributed search architecture",
        cluster=FakeCluster(healthy=1, total=3),
    )
    assert result.tier == "survival"
    assert result.selected_mode == "lexical"
    assert result.generation_allowed is False


def test_explicit_mode_is_never_overridden():
    result = make_plan(
        "HTTP_503",
        requested="semantic",
        inflight=63,
        capacity=64,
        cluster=FakeCluster(healthy=1, total=3, p95=5000),
    )
    assert result.tier == "manual"
    assert result.selected_mode == "semantic"
    assert result.generation_allowed is True
