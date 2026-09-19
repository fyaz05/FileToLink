"""Regression: deferred requeue must park the worker pool, not busy-spin."""

import time

import pytest

from Thunder.utils.rate_limiter import RateLimiter


@pytest.mark.unit
async def test_all_deferred_parks_pool():
    rl = RateLimiter()
    rl.request_event.set()

    async def noop(*a, **k):
        pass

    request = {
        "func": noop,
        "user_id": 123,
        "args": (),
        "kwargs": {},
        "not_before": time.time() + 60,
    }
    await rl._requeue_request(request, "regular", delay=60)

    # worker pops the deferred item, rotates it -- pool must park
    handled = await rl._process_one()
    assert handled is True
    assert rl.request_event.is_set() is False  # parked: no busy-spin
    assert rl._deferred_timer is not None
    assert len(rl.request_queue) == 1  # item still queued

    # a new enqueue wakes the pool immediately (timer + event both work)
    rl.request_event.set()
    rl._deferred_timer.cancel()
    rl._deferred_timer = None
    await rl.shutdown()


@pytest.mark.unit
async def test_runnable_item_prevents_parking():
    rl = RateLimiter()
    rl.request_event.set()

    async def noop(*a, **k):
        pass

    deferred = {
        "func": noop,
        "user_id": 1,
        "args": (),
        "kwargs": {},
        "not_before": time.time() + 60,
    }
    await rl._requeue_request(deferred, "regular", delay=60)

    runnable = {
        "func": noop,
        "user_id": 2,
        "args": (),
        "kwargs": {},
        "not_before": 0.0,
    }
    async with rl.request_lock:
        rl.request_queue.append(runnable)

    handled = await rl._process_one()  # pops deferred -> rotates
    assert handled is True
    assert rl.request_event.is_set() is True  # NOT parked: runnable item exists
    await rl.shutdown()
