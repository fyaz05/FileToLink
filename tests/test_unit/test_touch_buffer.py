"""Buffered file touches must survive a cancelled flush.

The touch buffer is drained at shutdown, and shutdown cancels background
tasks. `_bulk_flush` snapshots the buffer *before* awaiting the bulk write,
so a cancellation landing mid-write used to drop those deltas on the floor:
the buffer was already cleared and `except Exception` cannot catch
`CancelledError`. These tests pin the re-queue behaviour.
"""

import asyncio
from collections import OrderedDict

import pytest

from Thunder.utils import canonical_files as cf


class _FakeDb:
    """Database stand-in that can block mid-write to open the race window."""

    def __init__(self, block: asyncio.Event | None = None) -> None:
        self.calls: list[list[tuple[str, int, int]]] = []
        self._block = block
        self.entered = asyncio.Event()

    async def bulk_touch_file_records(self, rows):
        self.calls.append(list(rows))
        self.entered.set()
        if self._block is not None:
            await self._block.wait()


@pytest.fixture
def touch_env(monkeypatch):
    """Isolate the module's touch state from the rest of the suite."""
    monkeypatch.setattr(cf, "db", _FakeDb())
    monkeypatch.setattr(cf, "_pending_touches", OrderedDict())
    monkeypatch.setattr(cf, "_flush_task", None)
    monkeypatch.setattr(cf, "_dropped_touches", 0)
    monkeypatch.setattr(cf, "_cache_by_hash", OrderedDict())
    monkeypatch.setattr(cf, "_cache_by_unique_id", OrderedDict())
    monkeypatch.setattr(cf, "_FLUSH_DELAY_SECONDS", 0)
    return cf.db


def _record(public_hash: str) -> dict:
    return {"public_hash": public_hash, "file_unique_id": f"u-{public_hash}", "seen_count": 0}


@pytest.mark.unit
async def test_cancelled_flush_keeps_deltas(monkeypatch):
    """Cancelling the flush task mid-write must not empty the buffer."""
    block = asyncio.Event()
    fake = _FakeDb(block=block)
    monkeypatch.setattr(cf, "db", fake)
    monkeypatch.setattr(cf, "_pending_touches", OrderedDict())
    monkeypatch.setattr(cf, "_flush_task", None)
    monkeypatch.setattr(cf, "_dropped_touches", 0)
    monkeypatch.setattr(cf, "_cache_by_hash", OrderedDict())
    monkeypatch.setattr(cf, "_cache_by_unique_id", OrderedDict())
    monkeypatch.setattr(cf, "_FLUSH_DELAY_SECONDS", 0)

    public_hash = "a" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash), reused=True)
    await fake.entered.wait()  # the bulk write is now in flight

    assert cf._flush_task is not None
    cf._flush_task.cancel()
    # the flush task absorbs its own cancellation by design, so it completes
    await cf._flush_task
    assert cf._flush_task is None

    buffered = cf._pending_touches.get(public_hash)
    assert buffered is not None, "cancelled flush dropped the buffered deltas"
    assert buffered[1:] == (1, 1)  # reuse + seen survive


@pytest.mark.unit
async def test_drain_writes_back_requeued_deltas(monkeypatch):
    """After a cancelled flush, the shutdown drain must persist the deltas."""
    block = asyncio.Event()
    fake = _FakeDb(block=block)
    monkeypatch.setattr(cf, "db", fake)
    monkeypatch.setattr(cf, "_pending_touches", OrderedDict())
    monkeypatch.setattr(cf, "_flush_task", None)
    monkeypatch.setattr(cf, "_dropped_touches", 0)
    monkeypatch.setattr(cf, "_cache_by_hash", OrderedDict())
    monkeypatch.setattr(cf, "_cache_by_unique_id", OrderedDict())
    monkeypatch.setattr(cf, "_FLUSH_DELAY_SECONDS", 0)

    public_hash = "b" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash))
    await fake.entered.wait()

    cf._flush_task.cancel()
    await cf._flush_task
    block.set()  # let the in-flight write (and any drain) proceed

    await cf.drain_background_touch_tasks()

    # the first write was abandoned by the cancellation, so the drain has to
    # write the re-queued delta again -- an over-count is the accepted trade for
    # never dropping a touch (see _bulk_flush's note on partial writes)
    assert len(fake.calls) == 2, f"drain never rewrote the re-queued delta: {fake.calls}"
    assert fake.calls[-1] == [(public_hash, 0, 1)]
    assert not cf._pending_touches


@pytest.mark.unit
async def test_failed_flush_keeps_deltas(touch_env):
    """A failed write is still retried on the next flush (pre-existing behaviour)."""

    async def _boom(rows):
        raise RuntimeError("mongo down")

    touch_env.bulk_touch_file_records = _boom
    public_hash = "c" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash))

    await cf._bulk_flush()

    assert cf._pending_touches.get(public_hash) is not None


@pytest.mark.unit
async def test_concurrent_touches_merge_into_one_row(touch_env):
    """Two touches for the same file collapse into summed deltas, one row."""
    public_hash = "d" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash))
    cf.schedule_touch_file_record(_record(public_hash), reused=True)

    assert len(cf._pending_touches) == 1
    await cf._bulk_flush()

    assert touch_env.calls == [[(public_hash, 1, 2)]]
    assert not cf._pending_touches
