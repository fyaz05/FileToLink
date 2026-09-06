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
