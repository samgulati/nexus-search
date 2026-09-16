from __future__ import annotations

import re

from .config import settings
from .models import SearchPlan
from .observability import AUTOPILOT_DECISIONS

_IDENTIFIER_RE = re.compile(
    r"[A-Za-z0-9]+[_./:-][A-Za-z0-9_.:/-]+"
)
_QUOTED_RE = re.compile(r"[\"'][^\"']+[\"']")


class AdaptiveSearchController:
    def _profile(self, query: str) -> tuple[str, int]:
        tokens = [t for t in re.findall(r"[A-Za-z0-9_.:/-]+", query) if t]
        exact = bool(_QUOTED_RE.search(query) or _IDENTIFIER_RE.search(query))
        if exact or len(tokens) <= 2:
            return "exact", len(tokens)
        return "natural_language", len(tokens)

    async def plan(self, *, query: str, requested_mode: str, inflight: int, capacity: int, cluster_service) -> SearchPlan:
        profile, token_count = self._profile(query)
        capacity = max(1, int(capacity))
        load_ratio = min(1.0, max(0.0, inflight / capacity))

        if cluster_service.is_coordinator and cluster_service.shard_targets:
            total_shards = len(cluster_service.shard_targets)
            healthy_shards = max(0, min(total_shards, int(cluster_service.last_healthy_shards)))
            circuit_states = await cluster_service.circuit_state_counts()
            unavailable_circuits = circuit_states.get("open", 0) + circuit_states.get("half_open", 0)
        else:
            total_shards = 1
            healthy_shards = 1
            unavailable_circuits = 0

        health_ratio = healthy_shards / max(1, total_shards)
        observed_p95 = cluster_service.observed_p95_ms()
        budget = max(1.0, settings.autopilot_latency_budget_ms)

        if requested_mode != "auto" or not settings.autopilot_enabled:
            selected = requested_mode if requested_mode != "auto" else "hybrid"
            reasons = ["explicit retrieval-mode override"] if requested_mode != "auto" else ["autopilot disabled by configuration"]
            plan = SearchPlan(
                requested_mode=requested_mode,
                selected_mode=selected,
                tier="manual",
                query_profile=profile,
                query_tokens=token_count,
                reasons=reasons,
                healthy_shards=healthy_shards,
                total_shards=total_shards,
                unavailable_circuits=unavailable_circuits,
                inflight=inflight,
                capacity=capacity,
                load_ratio=round(load_ratio, 3),
                observed_p95_ms=round(observed_p95, 3),
                latency_budget_ms=round(budget, 3),
                generation_allowed=True,
            )
            AUTOPILOT_DECISIONS.labels(plan.tier, plan.selected_mode, plan.query_profile).inc()
            return plan

        reasons: list[str] = []
        severe_health = total_shards > 1 and health_ratio < settings.autopilot_min_health_ratio
        severe_pressure = load_ratio >= settings.autopilot_emergency_load_ratio
        severe_latency = observed_p95 > 0 and observed_p95 >= budget * 2

        constrained_health = total_shards > 1 and healthy_shards < total_shards
        constrained_pressure = load_ratio >= settings.autopilot_warn_load_ratio
        constrained_latency = observed_p95 > budget

        if severe_health or severe_pressure or severe_latency:
            tier = "survival"
            selected = "lexical"
            generation_allowed = False
            if severe_health:
                reasons.append(f"healthy shard ratio {healthy_shards}/{total_shards} below {settings.autopilot_min_health_ratio:.2f}")
            if severe_pressure:
                reasons.append(f"inflight utilization {load_ratio:.0%} above emergency threshold")
            if severe_latency:
                reasons.append(f"observed p95 {observed_p95:.0f} ms exceeds 2x latency budget")
        elif constrained_health or unavailable_circuits > 0 or constrained_pressure or constrained_latency:
            tier = "balanced"
            selected = "lexical" if profile == "exact" else "hybrid"
            generation_allowed = not (
                load_ratio >= settings.autopilot_generation_cutoff_load_ratio
                or health_ratio < settings.autopilot_min_health_ratio
            )
            if constrained_health:
                reasons.append(f"cluster degraded to {healthy_shards}/{total_shards} healthy shards")
            if unavailable_circuits:
                reasons.append(f"{unavailable_circuits} shard circuit(s) unavailable/probing")
            if constrained_pressure:
                reasons.append(f"inflight utilization {load_ratio:.0%} above warning threshold")
            if constrained_latency:
                reasons.append(f"observed p95 {observed_p95:.0f} ms above {budget:.0f} ms budget")
        else:
            tier = "quality"
            selected = "lexical" if profile == "exact" else "hybrid"
            generation_allowed = True
            if profile == "exact":
                reasons.append("exact/identifier-style query favors lexical precision")
            else:
                reasons.append("healthy cluster and latency budget allow hybrid retrieval")

        plan = SearchPlan(
            requested_mode="auto",
            selected_mode=selected,
            tier=tier,
            query_profile=profile,
            query_tokens=token_count,
            reasons=reasons,
            healthy_shards=healthy_shards,
            total_shards=total_shards,
            unavailable_circuits=unavailable_circuits,
            inflight=inflight,
            capacity=capacity,
            load_ratio=round(load_ratio, 3),
            observed_p95_ms=round(observed_p95, 3),
            latency_budget_ms=round(budget, 3),
            generation_allowed=generation_allowed,
        )
        AUTOPILOT_DECISIONS.labels(plan.tier, plan.selected_mode, plan.query_profile).inc()
        return plan


adaptive_search_controller = AdaptiveSearchController()
