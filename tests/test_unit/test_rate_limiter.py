# tests/test_unit/test_rate_limiter.py
"""H6: window math, breaker, sweep -- pure logic, no Mongo."""

import time

import pytest

from Thunder.utils.rate_limiter import TokenBucket, rate_limiter


@pytest.mark.unit
async def test_check_limits_window():
    rl = rate_limiter
    rl.enabled = True
    rl._initialization_error = False
    rl.max_requests_per_period = 2
    rl.rate_limit_period_seconds = 60
    rl.global_rate_limit_enabled = False

    uid = 90_001
    rl.user_requests.pop(uid, None)

    assert await rl.check_limits(uid, record=True) is True
    assert await rl.check_limits(uid, record=True) is True
    # window exhausted
    assert await rl.check_limits(uid, record=True) is False
    # advisory check does not extend the window
    assert await rl.check_limits(uid, record=False) is False
    assert len(rl.user_requests[uid]) == 2
    rl.user_requests.pop(uid, None)


@pytest.mark.unit
async def test_owner_bypass():
    from Thunder.vars import Var

    rl = rate_limiter
    rl.enabled = True
    assert await rl.check_limits(Var.OWNER_ID, record=True) is True


@pytest.mark.unit
async def test_sweep_prunes_stale_users():
    rl = rate_limiter
    rl.enabled = True
    rl.rate_limit_period_seconds = 60
    uid = 90_002
    old = time.time() - 3600
    rl.user_requests[uid] = _deque(old, old)
    stats = await rl.sweep()
    assert uid not in rl.user_requests
    assert stats["user_windows"] >= 1


def _deque(*timestamps):
    from collections import deque

    return deque(timestamps)


@pytest.mark.unit
class TestTokenBucket:
    async def test_burst_then_deny(self):
        bucket = TokenBucket(rate_per_second=2.0, burst_multiplier=2.0)
        allowed = 0
        for _ in range(10):
            if bucket.allow():
                allowed += 1
        assert allowed == int(bucket.burst)  # burst = 2x rate = 4

    async def test_refill_over_time(self):
        bucket = TokenBucket(rate_per_second=100.0, burst_multiplier=1.0)
        while bucket.allow():
            pass
        time.sleep(0.05)  # ~5 tokens
        assert bucket.allow() is True

    async def test_retry_after_positive_when_denied(self):
        bucket = TokenBucket(rate_per_second=0.5, burst_multiplier=1.0)
        while bucket.allow():
            pass
        assert bucket.retry_after() > 0

    async def test_zero_rate_always_allows(self):
        bucket = TokenBucket(rate_per_second=0.0)
        for _ in range(50):
            assert bucket.allow() is True
        assert bucket.retry_after() == 0.0


@pytest.mark.unit
async def test_occupancy_shape():
    occ = rate_limiter.occupancy()
    assert {"queued", "tracked_users", "global_window", "breaker_tokens"} <= set(occ)


@pytest.mark.unit
async def test_immediate_path_consumes_breaker_token(monkeypatch):
    """H6c (revised): fresh-user bursts execute inline, so the immediate path
    must consume a breaker token BEFORE running the handler."""
    from types import SimpleNamespace

    import Thunder.utils.rate_limiter as rl_mod

    rl = rate_limiter
    rl.enabled = True
    rl._initialization_error = False
    rl.max_requests_per_period = 100
    rl.rate_limit_period_seconds = 60
    rl.global_rate_limit_enabled = False
    monkeypatch.setattr(rl, "is_owner", lambda user_id: False)
    monkeypatch.setattr(rl, "breaker", TokenBucket(rate_per_second=5.0, burst_multiplier=2.0))

    uid = 90_100
    rl.user_requests.pop(uid, None)

    executed: list = []

    async def handler(bot, message, *args, **kwargs):
        executed.append(message)

    msg = SimpleNamespace(from_user=SimpleNamespace(id=uid), document=None)

    before = rl.breaker.available()
    await rl_mod.handle_rate_limited_request(None, msg, handler)
    assert len(executed) == 1
    assert rl.breaker.available() < before  # one token consumed on the spot
    rl.user_requests.pop(uid, None)


@pytest.mark.unit
async def test_dry_breaker_defers_immediate_request_to_queue(monkeypatch):
    """A dry breaker must not drop the request: it falls through to the
    queue, where workers consume tokens at exec time (shaping, not shedding)."""
    from types import SimpleNamespace

    import Thunder.utils.rate_limiter as rl_mod

    rl = rate_limiter
    rl.enabled = True
    rl._initialization_error = False
    rl.max_requests_per_period = 100
    rl.rate_limit_period_seconds = 60
    rl.global_rate_limit_enabled = False
    monkeypatch.setattr(rl, "is_owner", lambda user_id: False)
    monkeypatch.setattr(rl, "breaker", TokenBucket(rate_per_second=5.0, burst_multiplier=2.0))
    monkeypatch.setattr(rl, "get_user_priority", _async_return("regular"))
    monkeypatch.setattr(rl_mod, "send_queue_notification", _async_noop)
    monkeypatch.setattr(rl_mod, "send_queue_full_message", _async_noop)

    queued: list = []

    async def fake_add_to_queue(handler, user_id, file_identifier, bot, message, *args, **kwargs):
        queued.append(user_id)

    monkeypatch.setattr(rl, "add_to_queue", fake_add_to_queue)

    # drain the bucket completely
    while rl.breaker.allow():
        pass

    uid = 90_101
    rl.user_requests.pop(uid, None)

    executed: list = []

    async def handler(bot, message, *args, **kwargs):
        executed.append(message)

    msg = SimpleNamespace(from_user=SimpleNamespace(id=uid), document=None)

    await rl_mod.handle_rate_limited_request(None, msg, handler)
    assert executed == []
    assert queued == [uid]
    rl.user_requests.pop(uid, None)


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


async def _async_noop(*args, **kwargs):
    return None
