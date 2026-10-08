"""The durable worker dispatches as soon as it is woken, not only on its poll."""

from __future__ import annotations

import asyncio

from seleric_swarm.recovery import DurablePollingRunQueue, RunRecoveryWorker


class _Runs:
    pass


async def test_wake_triggers_a_dispatch_before_the_poll_interval(monkeypatch):
    worker = RunRecoveryWorker(_Runs(), executor=None, worker_id="w")  # type: ignore[arg-type]
    dispatches: list[float] = []
    stop = asyncio.Event()

    async def _dispatch(slots, in_flight, *, limit):
        dispatches.append(asyncio.get_running_loop().time())
        if len(dispatches) == 2:
            stop.set()
        return 0

    monkeypatch.setattr(worker, "_dispatch", _dispatch)
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, worker.wake.set)
    started = loop.time()
    await asyncio.wait_for(worker.run_forever(poll_interval_s=30, stop=stop), timeout=2)
    assert len(dispatches) == 2 and dispatches[1] - started < 1.0


async def test_enqueue_without_an_engine_is_a_no_op():
    await DurablePollingRunQueue().enqueue("run-1")
