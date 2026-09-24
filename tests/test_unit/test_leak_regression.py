"""Leak regression tests: cancellation must release, never strand.

Hermetic (no network, no Mongo): fresh RateLimiter instances, a fake db for
the broadcast prune path, and task snapshots around worker lifecycles.
"""

import asyncio
import time

import pytest

import Thunder.utils.broadcast as broadcast_module
from Thunder.utils.broadcast import _prune_collected
from Thunder.utils.rate_limiter import RateLimiter

pytestmark = pytest.mark.unit


def _deferred_item(delay=60.0):
    return {"user_id": 7, "not_before": time.time() + delay, "charged": False}


@pytest.mark.unit
async def test_park_arms_timer_and_shutdown_releases_it():
    """Parked pool (event cleared, timer armed) must unwind fully on shutdown:
    timer cancelled + nulled, queues drained, event cleared."""
    rl = RateLimiter()
    async with rl.request_lock:
        rl.request_queue.append(_deferred_item())
        rl._park_if_all_deferred()
        assert not rl.request_event.is_set()
        assert rl._deferred_timer is not None

    await rl.shutdown()
    assert rl._deferred_timer is None
    assert len(rl.request_queue) == 0 and len(rl.priority_queue) == 0
    assert not rl.request_event.is_set()


@pytest.mark.unit
async def test_executor_worker_cancel_terminates():
    """Cancelling a parked executor worker must end the task (no hang)."""
    rl = RateLimiter()
    before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    worker = asyncio.create_task(rl.request_executor())
    for _ in range(10):
        await asyncio.sleep(0)  # let it reach event.wait()
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass  # expected: worker breaks its loop on cancel
    assert worker.done()
    after = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert not (after - before - {worker}), "leaked tasks after worker cancel"
    await rl.shutdown()


@pytest.mark.unit
async def test_prune_collected_best_effort_and_bounded(monkeypatch):
    """Background prune attempts every id, survives single failures, ends."""
    attempted: list[int] = []

    class _FakeDB:
        async def delete_user(self, user_id: int) -> None:
            attempted.append(user_id)
            if user_id == 2:
                raise RuntimeError("mongo down")

    monkeypatch.setattr(broadcast_module, "db", _FakeDB())
    before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    task = asyncio.create_task(_prune_collected([1, 2, 3]))
    await asyncio.wait_for(task, timeout=5)
    assert attempted == [1, 2, 3]
    after = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert not (after - before), "leaked tasks after prune"
