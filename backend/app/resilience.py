from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class CircuitSnapshot:
    state: str
    failures: int
    retry_after_seconds: float


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: float) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.1, recovery_seconds)
        self.failures = 0
        self.opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    async def allow_request(self) -> bool:
        async with self._lock:
            if self.opened_at is None:
                return True
            elapsed = time.monotonic() - self.opened_at
            if elapsed < self.recovery_seconds:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    async def record_success(self) -> None:
        async with self._lock:
            self.failures = 0
            self.opened_at = None
            self._probe_in_flight = False

    async def record_failure(self) -> None:
        async with self._lock:
            self.failures += 1
            self._probe_in_flight = False
            if self.failures >= self.failure_threshold:
                self.opened_at = time.monotonic()

    async def snapshot(self) -> CircuitSnapshot:
        async with self._lock:
            if self.opened_at is None:
                return CircuitSnapshot("closed", self.failures, 0.0)
            elapsed = time.monotonic() - self.opened_at
            remaining = max(0.0, self.recovery_seconds - elapsed)
            return CircuitSnapshot("open" if remaining > 0 else "half_open", self.failures, remaining)
