import asyncio

from app.resilience import CapacityGate


def test_capacity_gate_rejects_without_queueing():
    async def scenario():
        gate = CapacityGate(2)
        assert await gate.try_acquire()
        assert await gate.try_acquire()
        assert not await gate.try_acquire()
        assert await gate.inflight() == 2

        await gate.release()
        assert await gate.try_acquire()
        assert await gate.inflight() == 2

        await gate.release()
        await gate.release()
        assert await gate.inflight() == 0

    asyncio.run(scenario())
