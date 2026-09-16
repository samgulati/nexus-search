import asyncio

from app.resilience import CircuitBreaker


def test_circuit_opens_after_threshold():
    async def scenario():
        breaker = CircuitBreaker(2, 60)
        assert await breaker.allow_request()
        await breaker.record_failure()
        assert await breaker.allow_request()
        await breaker.record_failure()
        assert not await breaker.allow_request()
        snapshot = await breaker.snapshot()
        assert snapshot.state == "open"
        assert snapshot.failures == 2
    asyncio.run(scenario())


def test_circuit_half_open_probe_recovers():
    async def scenario():
        breaker = CircuitBreaker(1, 0.1)
        await breaker.record_failure()
        assert not await breaker.allow_request()
        await asyncio.sleep(0.11)
        assert await breaker.allow_request()
        assert not await breaker.allow_request()
        await breaker.record_success()
        assert await breaker.allow_request()
        snapshot = await breaker.snapshot()
        assert snapshot.state == "closed"
    asyncio.run(scenario())
