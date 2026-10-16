"""Charge-at-exec (AGENTS.md H6b) -- the invariant the queue is built on.

The sliding window must be charged exactly once, at execution time, never on
admission: a request that sits in the queue or is requeued after a FloodWait
must not burn the user's window on server-side failures. Nothing drove
_process_one on a *runnable* item before these tests, so the charge block
could be deleted and the whole suite still passed.
"""

import time

import pytest
from pyrogram.errors import FloodWait

from Thunder.utils import rate_limiter as rl_mod
from Thunder.utils.rate_limiter import TokenBucket, rate_limiter


def _arm(monkeypatch, *, period=60, max_requests=100, rps=1000.0):
    """Make the singleton behave like a healthy limiter with an empty window."""
    rl = rate_limiter
    rl.enabled = True
    rl._initialization_error = False
    rl.max_requests_per_period = max_requests
    rl.rate_limit_period_seconds = period
    rl.global_rate_limit_enabled = False
    monkeypatch.setattr(rl, "is_owner", lambda user_id: False)
    monkeypatch.setattr(rl, "breaker", TokenBucket(rate_per_second=rps, burst_multiplier=2.0))
    return rl


def _item(uid, handler, *, queue="request_queue"):
    return {
        "user_id": uid,
        "func": handler,
        "args": (),
        "kwargs": {},
        "file_identifier": None,
        "not_before": 0.0,
        "charged": False,
    }


@pytest.mark.unit
async def test_runnable_item_is_charged_once_at_execution(monkeypatch):
    rl = _arm(monkeypatch)
    uid = 90_301
    rl.user_requests.pop(uid, None)
    rl.request_queue.clear()
    rl.priority_queue.clear()

    ran = []

    async def handler(*args, **kwargs):
        ran.append(kwargs.get("notification_msg"))

    request = _item(uid, handler)
    request["kwargs"] = {"notification_msg": "notice"}
    rl.request_queue.append(request)

    assert await rl._process_one() is True

    assert ran, "runnable item never executed"
    assert len(rl.user_requests[uid]) == 1, "charge-at-exec did not record exactly one hit"
    assert request["charged"] is True

    rl.user_requests.pop(uid, None)
    rl.request_queue.clear()


@pytest.mark.unit
async def test_charged_item_is_not_charged_again(monkeypatch):
    """A FloodWait requeue must not re-charge: one upload burning the user's
    whole window on retries is the exact failure H6b exists to prevent."""
    rl = _arm(monkeypatch)
    uid = 90_302
    rl.user_requests.pop(uid, None)
    rl.request_queue.clear()
    rl.priority_queue.clear()

    async def always_floods(*args, **kwargs):
        raise FloodWait(1)

    request = _item(uid, always_floods)
    rl.request_queue.append(request)

    await rl._process_one()
    after_first = len(rl.user_requests[uid])
    assert after_first == 1
    assert request["charged"] is True
    # requeued with a delay; clear not_before so the next pass is runnable
    requeued = rl.request_queue or rl.priority_queue
    assert requeued, "FloodWait did not requeue the item"
    requeued[0]["not_before"] = 0.0

    await rl._process_one()

    assert len(rl.user_requests[uid]) == 1, "requeue re-charged the sliding window"

    rl.user_requests.pop(uid, None)
    rl.request_queue.clear()
    rl.priority_queue.clear()


@pytest.mark.unit
async def test_admission_does_not_charge_before_execution(monkeypatch):
    """check_limits(record=False) is advisory; the window only moves when the
    request actually runs."""
    rl = _arm(monkeypatch)
    uid = 90_303
    rl.user_requests.pop(uid, None)

    assert await rl.check_limits(uid, record=False) is True
    assert len(rl.user_requests[uid]) == 0

    assert await rl.check_limits(uid, record=True) is True
    assert len(rl.user_requests[uid]) == 1

    rl.user_requests.pop(uid, None)


@pytest.mark.unit
async def test_process_one_returns_false_on_an_empty_queue(monkeypatch):
    rl = _arm(monkeypatch)
    rl.request_queue.clear()
    rl.priority_queue.clear()
    assert await rl._process_one() is False


@pytest.mark.unit
async def test_deferred_item_is_not_charged(monkeypatch):
    """An item parked until later must not consume the window either."""
    rl = _arm(monkeypatch)
    uid = 90_304
    rl.user_requests.pop(uid, None)
    rl.request_queue.clear()
    rl.priority_queue.clear()

    async def handler(*args, **kwargs):
        raise AssertionError("deferred item must not run")

    request = _item(uid, handler)
    request["not_before"] = time.time() + 3600
    rl.request_queue.append(request)

    await rl._process_one()

    assert len(rl.user_requests.get(uid, ())) == 0, "deferred item was charged"
    rl.request_queue.clear()


@pytest.mark.unit
def test_module_exposes_max_attempts():
    assert rl_mod.MAX_REQUEST_ATTEMPTS >= 1
