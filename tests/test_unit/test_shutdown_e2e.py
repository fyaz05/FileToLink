"""End-to-end shutdown: the path CI never exercises.

The container fix (shared budget + floor per step) is only meaningful if
SIGTERM actually reaches ``shutdown_services`` and the touch buffer lands in
Mongo before the process exits. Docker is not available on every dev host, so
this drives ``shutdown_services`` directly with a populated buffer and a
cancel-friendly background task -- the real function, the real rate limiter,
the real touch buffer, only the database and the Telegram client are faked.

What this does NOT cover: PID 1 signal delivery, which is a property of
Dockerfile/thunder.sh. That is pinned separately by
``test_signal_delivery_is_wired_for_pid_1``.
"""

import asyncio

import pytest

from Thunder import __main__ as main_mod
from Thunder.utils import canonical_files as cf
from Thunder.utils import rate_limiter as rl


class _FakeDb:
    """Mirrors Database.bulk_touch_file_records: the kwarg and the bool."""

    def __init__(self) -> None:
        self.touched: list[tuple[str, int, int]] = []
        self.closed = False
        self.post_close_writes: list = []
        self.raise_on_error_seen: list[bool] = []

    async def bulk_touch_file_records(self, rows, *, raise_on_error: bool = False) -> bool:
        self.raise_on_error_seen.append(raise_on_error)
        if self.closed:
            # the real client raises once the pool is closed; a fake that
            # accepts writes after close() cannot detect a flush that happens
            # in the wrong order relative to db.close()
            self.post_close_writes.append(list(rows))
            raise RuntimeError("client is closed")
        self.touched.extend(rows)
        return True

    async def close(self):
        self.closed = True


@pytest.fixture
def shutdown_env(monkeypatch):
    """Wire shutdown_services to fakes and give back an observation bucket."""
    fake = _FakeDb()
    monkeypatch.setattr(cf, "db", fake)
    monkeypatch.setattr(cf, "_pending_touches", type(cf._pending_touches)())
    monkeypatch.setattr(cf, "_flush_task", None)
    monkeypatch.setattr(cf, "_dropped_touches", 0)
    monkeypatch.setattr(cf, "_cache_by_hash", type(cf._cache_by_hash)())
    monkeypatch.setattr(cf, "_cache_by_unique_id", type(cf._cache_by_unique_id)())
    monkeypatch.setattr(main_mod, "close_shortener", _noop)
    monkeypatch.setattr(main_mod, "cleanup_clients", _noop)
    monkeypatch.setattr(main_mod, "db", fake)
    monkeypatch.setattr(main_mod, "work_loads", {})
    # no rate-limiter executors running in a unit test
    monkeypatch.setattr(rl.rate_limiter, "shutdown", _noop)
    return fake


async def _noop():
    return None


def _arm_touch(cf_mod, public_hash: str) -> None:
    cf_mod.schedule_touch_file_record(
        {"public_hash": public_hash, "file_unique_id": "u", "seen_count": 0}, reused=True
    )


@pytest.mark.unit
async def test_shutdown_flushes_the_touch_buffer_and_closes_cleanly(shutdown_env):
    """The whole point: a populated buffer must reach Mongo during teardown,
    and the process must NOT exit non-zero (which would restart-loop a PaaS)."""
    _arm_touch(cf, "a" * cf.PUBLIC_HASH_LENGTH)

    assert cf._pending_touches, "fixture did not populate the buffer"

    await main_mod.shutdown_services(background_tasks=[], app_runner=None)

    assert shutdown_env.touched, "the touch buffer was never flushed on shutdown"
    assert (("a" * cf.PUBLIC_HASH_LENGTH), 1, 1) in shutdown_env.touched
    assert not cf._pending_touches
    assert shutdown_env.closed, "the database was not closed"
    # the fake rejects writes after close(), so this pins the ORDER, not just
    # that both happened: moving db.close() before the flush loses the deltas
    # with no error anywhere
    assert not shutdown_env.post_close_writes, (
        "the touch buffer was flushed after db.close() -- those deltas are lost"
    )
    assert shutdown_env.raise_on_error_seen == [True], (
        "the flush did not ask the client to raise on failure, so a Mongo "
        "brownout is reported as a clean shutdown"
    )


@pytest.mark.unit
async def test_shutdown_returns_normally_on_a_healthy_stop(shutdown_env):
    """A healthy stop returns without raising. sys.exit(1) here would
    restart-loop Heroku/Render on every clean deploy, so the only exit this
    path may take is a real, recorded failure."""

    assert await main_mod.shutdown_services(background_tasks=[], app_runner=None) is None


@pytest.mark.unit
async def test_shutdown_cancels_background_work_without_failing(shutdown_env):
    """Background tasks are cancelled first; a clean return must survive that,
    or every deploy looks like a crash."""
    started = asyncio.Event()

    async def _worker():
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(_worker())
    await started.wait()

    assert await main_mod.shutdown_services(background_tasks=[task], app_runner=None) is None
    assert task.done()


@pytest.mark.unit
async def test_hung_mongo_during_shutdown_is_reported_as_a_failure(shutdown_env, monkeypatch):
    """A touch flush that never answers must be *reported*, not silently
    swallowed: the drain shields its write, and swallowing the caller's
    cancellation would make wait_for return normally and mark the step clean.
    The budget still bounds the whole teardown.

    The step/budget caps are shrunk so the mechanism runs in ~0.2s instead of
    waiting out the real 8s cap. The production values are pinned separately,
    against the source, in test_shutdown_budget.py.
    """
    monkeypatch.setattr(main_mod, "_SHUTDOWN_BUDGET_SECONDS", 1.0)
    monkeypatch.setattr(main_mod, "_TEARDOWN_STEP_SECONDS", 0.2)
    monkeypatch.setattr(main_mod, "_TEARDOWN_FLOOR_SECONDS", 0.05)

    async def _hang(rows, *, raise_on_error: bool = False):
        await asyncio.sleep(30)

    monkeypatch.setattr(shutdown_env, "bulk_touch_file_records", _hang)
    _arm_touch(cf, "b" * cf.PUBLIC_HASH_LENGTH)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(SystemExit) as exc:
        await main_mod.shutdown_services(background_tasks=[], app_runner=None)
    elapsed = loop.time() - started

    assert exc.value.code == 1, "a step that hung must be reported, not swallowed"
    budget = main_mod._SHUTDOWN_BUDGET_SECONDS + 5 * main_mod._TEARDOWN_FLOOR_SECONDS
    assert elapsed < budget + 2, f"shutdown took {elapsed:.1f}s, over the {budget:.0f}s bound"
    assert elapsed < 60, "shutdown outlived the container stop grace period"


@pytest.mark.unit
def test_thunder_sh_execs_python_so_it_becomes_pid_1():
    """Docker runs CMD ["bash", "thunder.sh"]; without exec, bash stays PID 1 and
    never forwards SIGTERM, so shutdown_services is unreachable in every
    container deploy.

    This covers only our half of the chain. That pyrofork's idle() actually
    delivers the signal to a real process is proved for real, by sending
    SIGTERM to a live interpreter, in test_signal_delivery.test_sigterm_reaches_idle
    (integration tier: POSIX only).
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    script = (root / "thunder.sh").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert "exec " in script, "thunder.sh must exec python so it becomes PID 1"
    assert 'CMD ["bash", "thunder.sh"]' in dockerfile
    # the update phase runs under bash as PID 1; it must not block forever
    assert "update.py" in script
