from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fastapi import Request, Response


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class AnonymousTokenBucket:
    """Dependency-free process-local abuse backstop for anonymous public search."""

    def __init__(self, capacity: int, refill_per_second: float, max_clients: int = 20_000):
        self.capacity = max(1, capacity)
        self.refill_per_second = max(0.01, refill_per_second)
        self.max_clients = max(100, max_clients)
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_clients:
                    self._evict_oldest()
                bucket = _Bucket(tokens=float(self.capacity), updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(self.capacity),
                bucket.tokens + elapsed * self.refill_per_second,
            )
            bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0

            wait = max(1, int((1.0 - bucket.tokens) / self.refill_per_second) + 1)
            return False, wait

    def _evict_oldest(self) -> None:
        if not self._buckets:
            return
        count = max(1, len(self._buckets) // 10)
        oldest = sorted(self._buckets.items(), key=lambda item: item[1].updated_at)[:count]
        for key, _ in oldest:
            self._buckets.pop(key, None)


def anonymous_client_key(request: Request, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        cf_ip = request.headers.get('cf-connecting-ip')
        if cf_ip:
            return cf_ip.strip()
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            return forwarded.split(',', 1)[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return 'unknown'


def add_public_security_headers(response: Response, path: str) -> None:
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if path.startswith('/assets/'):
        response.headers.setdefault('Cache-Control', 'public, max-age=31536000, immutable')
    elif path in {'/', '/index.html'}:
        response.headers.setdefault('Cache-Control', 'public, max-age=0, must-revalidate')
