from __future__ import annotations

import asyncio

from app.public_guard import AnonymousTokenBucket


def test_anonymous_token_bucket_rejects_after_burst():
    limiter = AnonymousTokenBucket(capacity=2, refill_per_second=0.01)
    assert asyncio.run(limiter.allow('client-a'))[0] is True
    assert asyncio.run(limiter.allow('client-a'))[0] is True
    allowed, retry_after = asyncio.run(limiter.allow('client-a'))
    assert allowed is False
    assert retry_after >= 1


def test_anonymous_token_bucket_isolated_by_client():
    limiter = AnonymousTokenBucket(capacity=1, refill_per_second=0.01)
    assert asyncio.run(limiter.allow('client-a'))[0] is True
    assert asyncio.run(limiter.allow('client-b'))[0] is True
