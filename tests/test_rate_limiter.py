"""Rate limiter: priority, queue accounting and the per-user/global windows.

The limiter is disabled by default in production, so these tests drive it
directly with an enabled instance rather than through the bot.
"""
import asyncio
from types import SimpleNamespace

import pytest

from Thunder.utils.rate_limiter import QueueFullError, RateLimiter, handle_rate_limited_request
from Thunder.vars import Var


@pytest.fixture
def limiter(monkeypatch):
    """An enabled limiter with a small, predictable configuration."""
    instance = RateLimiter()
    instance.enabled = True
    instance.global_rate_limit_enabled = False
    instance.max_requests_per_period = 2
    instance.rate_limit_period_seconds = 60
    instance.max_queue_size = 3
    instance.max_global_requests_per_minute = 2
    instance._initialization_error = False
    instance.auth_cache.clear()
    instance.user_requests.clear()
    instance.global_requests.clear()
    instance.user_queue_counts.clear()
    return instance


class TestOwnershipAndPriority:
    def test_owner_is_recognised(self):
        assert RateLimiter().is_owner(Var.OWNER_ID) is True

    def test_non_owner_is_not(self):
        assert RateLimiter().is_owner(Var.OWNER_ID + 1) is False

    async def test_owner_priority(self, limiter):
        assert await limiter.get_user_priority(Var.OWNER_ID) == "owner"

    async def test_authorized_user_priority(self, limiter, monkeypatch):
        async def fake_find_one(query):
            return {"user_id": query["user_id"]}

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)
        assert await limiter.get_user_priority(999) == "authorized"

    async def test_regular_user_priority(self, limiter, monkeypatch):
        async def fake_find_one(query):
            return None

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)
        assert await limiter.get_user_priority(999) == "regular"


class TestCheckLimits:
    async def test_owner_bypasses_limits(self, limiter):
        for _ in range(10):
            assert await limiter.check_limits(Var.OWNER_ID) is True

    async def test_disabled_limiter_always_allows(self, limiter):
        limiter.enabled = False
        for _ in range(10):
            assert await limiter.check_limits(999) is True

    async def test_allows_up_to_the_period_limit(self, limiter):
        assert await limiter.check_limits(999) is True
        assert await limiter.check_limits(999) is True
        assert await limiter.check_limits(999) is False

    async def test_window_expiry_frees_capacity(self, limiter, monkeypatch):
        assert await limiter.check_limits(999) is True
        assert await limiter.check_limits(999) is True
        assert await limiter.check_limits(999) is False

        import Thunder.utils.rate_limiter as rl
        real_time = rl.time.time
        monkeypatch.setattr(rl.time, "time", lambda: real_time() + 61)
        assert await limiter.check_limits(999) is True

    async def test_record_false_does_not_consume_quota(self, limiter):
        for _ in range(5):
            assert await limiter.check_limits(999, record=False) is True
        # Nothing was recorded, so a full allowance is still available.
        assert await limiter.check_limits(999) is True
        assert await limiter.check_limits(999) is True
        assert await limiter.check_limits(999) is False

    async def test_global_limit_applies_across_users(self, limiter):
        limiter.global_rate_limit_enabled = True
        assert await limiter.check_limits(1) is True
        assert await limiter.check_limits(2) is True
        assert await limiter.check_limits(3) is False

    async def test_per_user_limit_is_independent(self, limiter):
        assert await limiter.check_limits(1) is True
        assert await limiter.check_limits(1) is True
        assert await limiter.check_limits(1) is False
        # A different user still has their own allowance.
        assert await limiter.check_limits(2) is True


class TestQueueing:
    async def test_disabled_limiter_runs_immediately(self, limiter):
        limiter.enabled = False
        called = []

        async def work():
            called.append(True)

        await limiter.add_to_queue(work, 999)
        assert called == [True]

    async def test_owner_and_regular_land_in_separate_queues(self, limiter, monkeypatch):
        async def fake_find_one(query):
            return None

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)

        async def work():
            pass

        await limiter.add_to_queue(work, Var.OWNER_ID)
        await limiter.add_to_queue(work, 999)
        assert len(limiter.request_queue) == 2
        assert len(limiter.priority_queue) == 0

    async def test_authorized_user_gets_priority_queue(self, limiter, monkeypatch):
        async def fake_find_one(query):
            return {"user_id": query["user_id"]}

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)

        async def work():
            pass

        await limiter.add_to_queue(work, 999)
        assert len(limiter.priority_queue) == 1
        assert len(limiter.request_queue) == 0

    async def test_queue_full_raises(self, limiter, monkeypatch):
        async def fake_find_one(query):
            return None

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)

        async def work():
            pass

        for _ in range(limiter.max_queue_size):
            await limiter.add_to_queue(work, 999)
        with pytest.raises(QueueFullError):
            await limiter.add_to_queue(work, 999)

    async def test_shutdown_clears_queues(self, limiter, monkeypatch):
        async def fake_find_one(query):
            return None

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)

        async def work():
            pass

        await limiter.add_to_queue(work, 999)
        await limiter.shutdown()
        assert len(limiter.request_queue) == 0
        assert len(limiter.priority_queue) == 0
        assert limiter.user_queue_counts == {}

    async def test_shutdown_during_priority_lookup_does_not_enqueue(self, limiter, monkeypatch):
        started = asyncio.Event()
        release = asyncio.Event()

        async def priority(user_id):
            started.set()
            await release.wait()
            return "regular"

        async def work():
            pass

        monkeypatch.setattr(limiter, "get_user_priority", priority)
        task = asyncio.create_task(limiter.add_to_queue(work, 999))
        await started.wait()
        await limiter.shutdown()
        release.set()
        with pytest.raises(RuntimeError, match="shut down"):
            await task
        assert not limiter.request_queue
        assert not limiter.priority_queue
        assert limiter.user_queue_counts == {}

    async def test_direct_handlers_do_not_run_after_shutdown(self, limiter, monkeypatch):
        import Thunder.utils.rate_limiter as module

        called = []

        async def work(*args, **kwargs):
            called.append(True)

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=Var.OWNER_ID),
            document=None,
        )
        monkeypatch.setattr(module, "rate_limiter", limiter)
        await limiter.shutdown()
        await handle_rate_limited_request(object(), message, work)
        assert called == []

    async def test_shutdown_during_queue_notification_does_not_send_queue_full(
        self, limiter, monkeypatch
    ):
        import Thunder.utils.rate_limiter as module

        notification_started = asyncio.Event()
        release_notification = asyncio.Event()
        queue_full_calls = []

        async def no_limit(user_id, record=True):
            return False

        async def priority(user_id):
            return "regular"

        async def notify(*args, **kwargs):
            notification_started.set()
            await release_notification.wait()
            return object()

        async def queue_full(*args, **kwargs):
            queue_full_calls.append(True)

        async def work(*args, **kwargs):
            pass

        message = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            document=None,
        )
        monkeypatch.setattr(module, "rate_limiter", limiter)
        monkeypatch.setattr(limiter, "check_limits", no_limit)
        monkeypatch.setattr(limiter, "get_user_priority", priority)
        monkeypatch.setattr(module, "send_queue_notification", notify)
        monkeypatch.setattr(module, "send_queue_full_message", queue_full)
        task = asyncio.create_task(handle_rate_limited_request(object(), message, work))
        await notification_started.wait()
        await limiter.shutdown()
        release_notification.set()
        await task
        assert queue_full_calls == []
        assert not limiter.request_queue
        assert not limiter.priority_queue


class TestExecutor:
    async def test_shutdown_lets_in_flight_work_finish(self, limiter):
        started = asyncio.Event()
        release = asyncio.Event()
        completed = []

        async def work():
            started.set()
            await release.wait()
            completed.append(True)

        await limiter.add_to_queue(work, Var.OWNER_ID)
        task = asyncio.create_task(limiter.request_executor())
        await started.wait()
        await limiter.shutdown()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert completed == [True]

    async def test_priority_queue_drains_before_regular(self, limiter, monkeypatch):
        order = []

        async def fake_find_one(query):
            return {"user_id": query["user_id"]}

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)

        async def regular():
            order.append("regular")

        async def priority():
            order.append("priority")

        # Bypass the priority lookup for the regular one.
        async def no_auth(query):
            return None

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            no_auth)
        await limiter.add_to_queue(regular, 1001)

        monkeypatch.setattr(
            "Thunder.utils.rate_limiter.db.authorized_users_col.find_one",
            fake_find_one)
        await limiter.add_to_queue(priority, 1002)

        task = asyncio.create_task(limiter.request_executor())
        for _ in range(80):
            await asyncio.sleep(0.05)
            if len(order) >= 2:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert order == ["priority", "regular"], order
