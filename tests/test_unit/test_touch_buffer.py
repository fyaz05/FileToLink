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
    """Database stand-in that can block mid-write to open the race window.

    Mirrors Database.bulk_touch_file_records' signature and return contract,
    including ``raise_on_error``. An earlier version took only ``rows``, so a
    TypeError from the unexpected kwarg was re-queued forever and the flush
    loop spun -- the fake had drifted from the thing it stands in for.
    """

    def __init__(self, block: asyncio.Event | None = None) -> None:
        self.calls: list[list[tuple[str, int, int]]] = []
        self.raise_on_error_seen: list[bool] = []
        self._block = block
        self.entered = asyncio.Event()

    async def bulk_touch_file_records(self, rows, *, raise_on_error: bool = False) -> bool:
        self.calls.append(list(rows))
        self.raise_on_error_seen.append(raise_on_error)
        self.entered.set()
        if self._block is not None:
            await self._block.wait()
        return True


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
    """A failed write is still retried on the next flush (pre-existing behaviour).

    The stub honours ``raise_on_error`` exactly as Database.bulk_touch_file_
    records does. An earlier version raised unconditionally, which is NOT what
    the real callee does: it swallows every Mongo exception and returns False
    unless asked to re-raise. That mis-model is why a Blocking bug shipped --
    _bulk_flush omitted raise_on_error, so in production the exception never
    arrived, the re-queue never ran, and every buffered delta was lost -- while
    this test, against the raising stub, stayed green.
    """

    async def _fail_like_the_real_callee(rows, *, raise_on_error: bool = False):
        if raise_on_error:
            raise RuntimeError("mongo down")
        return False

    touch_env.bulk_touch_file_records = _fail_like_the_real_callee
    public_hash = "c" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash))

    await cf._bulk_flush()

    assert cf._pending_touches.get(public_hash) is not None, (
        "the delta was dropped: a Mongo failure silently loses seen/reuse "
        "counts while the in-memory cache has already been incremented"
    )


@pytest.mark.unit
async def test_flush_does_not_swallow_a_failed_write(touch_env):
    """Pin the call contract itself: the callee defaults to returning False,
    so _bulk_flush must ask it to raise or the failure is unobservable."""
    seen_kwargs = {}

    async def _capture(rows, *, raise_on_error: bool = False):
        seen_kwargs["raise_on_error"] = raise_on_error
        return True

    touch_env.bulk_touch_file_records = _capture
    public_hash = "e" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash))

    await cf._bulk_flush()

    assert seen_kwargs.get("raise_on_error") is True, (
        "bulk_touch_file_records is called without raise_on_error, so it "
        "swallows Mongo errors and its False return is discarded"
    )


@pytest.mark.unit
async def test_a_swallowed_write_is_detected_even_if_raise_on_error_is_dropped(touch_env):
    """Defence in depth: should the callee ever change back to returning False,
    _bulk_flush must not read that as success."""

    async def _return_false(rows, *, raise_on_error: bool = False):
        return False

    touch_env.bulk_touch_file_records = _return_false
    public_hash = "f" * cf.PUBLIC_HASH_LENGTH
    cf.schedule_touch_file_record(_record(public_hash))

    await cf._bulk_flush()

    assert cf._pending_touches.get(public_hash) is not None, (
        "a False return was treated as a successful flush; the delta is lost with no error anywhere"
    )


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


# --- the two bounds the comments claim ------------------------------------
#
# "memory stays capped" and "repeated failures must not grow the buffer" were
# both justified in the code by a comment and enforced by nothing: removing
# either while-loop left the suite green.


@pytest.mark.unit
async def test_the_buffer_is_capped(touch_env):
    from Thunder.utils import canonical_files as cf

    for i in range(cf._TOUCH_BUFFER_MAX + 250):
        cf.schedule_touch_file_record(_record(f"{i:032x}"[: cf.PUBLIC_HASH_LENGTH]))
        assert len(cf._pending_touches) <= cf._TOUCH_BUFFER_MAX, (
            f"the buffer grew to {len(cf._pending_touches)} entries, past the cap "
            f"of {cf._TOUCH_BUFFER_MAX}"
        )

    assert cf._dropped_touches >= 250, "the overflow was not counted"


@pytest.mark.unit
async def test_the_oldest_entries_drop_first(touch_env):
    """Dropping the NEWEST would discard the deltas we just took, which is worse
    than dropping the ones we have been failing to flush longest."""
    from Thunder.utils import canonical_files as cf

    total = cf._TOUCH_BUFFER_MAX + 10
    for i in range(total):
        cf.schedule_touch_file_record(_record(f"{i:032x}"[: cf.PUBLIC_HASH_LENGTH]))

    keys = list(cf._pending_touches)
    assert len(keys) == cf._TOUCH_BUFFER_MAX
    expected = [f"{i:032x}"[: cf.PUBLIC_HASH_LENGTH] for i in range(10, total)]
    assert keys == expected, "the wrong end of the buffer was dropped"


@pytest.mark.unit
async def test_the_hottest_key_is_not_the_one_evicted(touch_env):
    """Assigning to an existing OrderedDict key does NOT reorder it.

    That made this a silent data-loss regression: the file touched most often
    stayed at position 0 and was the first thing popitem(last=False) threw away,
    taking every delta it had accumulated with it, while files touched once each
    were kept. A test that only ever inserts never-repeated keys cannot see this,
    because for them insertion order and touch order are the same thing.
    """
    from Thunder.utils import canonical_files as cf

    keys = [f"{i:032x}"[: cf.PUBLIC_HASH_LENGTH] for i in range(cf._TOUCH_BUFFER_MAX)]
    for k in keys:
        cf.schedule_touch_file_record(_record(k))

    hot = keys[0]
    for _ in range(50):
        cf.schedule_touch_file_record(_record(hot))

    assert cf._pending_touches[hot][2] == 51, "the delta did not accumulate"
    assert list(cf._pending_touches).index(hot) == len(cf._pending_touches) - 1, (
        "the most-touched file is still at the FRONT of the buffer -- "
        "re-touching it did not move it to the back, so it is next to be evicted"
    )

    # one new key forces exactly one eviction
    cf.schedule_touch_file_record(_record("ff" * 16))

    assert hot in cf._pending_touches, (
        "the most frequently touched file was evicted while four-thousand files "
        "touched once each were kept -- its accumulated deltas are gone"
    )
    assert cf._dropped_touches == 1


@pytest.mark.unit
async def test_a_requeued_entry_is_not_evicted_by_the_next_overflow(touch_env):
    """_requeue_touches re-inserts an entry that keeps failing to flush. Without
    moving it to the back it sits at the front and the trim below drops it on the
    very next overflow -- so the entry that failed most is the first lost, while
    live arrivals at the back survive.
    """
    from Thunder.utils import canonical_files as cf

    keys = [f"{i:032x}"[: cf.PUBLIC_HASH_LENGTH] for i in range(cf._TOUCH_BUFFER_MAX)]
    for k in keys:
        cf.schedule_touch_file_record(_record(k))

    doomed = keys[0]
    before = cf._pending_touches[doomed]
    cf._requeue_touches([(doomed, before)])

    assert list(cf._pending_touches).index(doomed) == len(cf._pending_touches) - 1, (
        "the re-queued entry is still at the front, so the next overflow drops it"
    )

    cf.schedule_touch_file_record(_record("ff" * 16))
    assert doomed in cf._pending_touches, "the persistently failing entry was evicted"


@pytest.mark.unit
async def test_a_repeatedly_failing_flush_does_not_grow_the_buffer(touch_env):
    """Re-queued deltas merge by key, so a persistently failing Mongo cannot make
    the buffer grow -- the merge is what bounds it, not the cap alone."""

    async def _fail_like_the_real_callee(rows, *, raise_on_error: bool = False):
        if raise_on_error:
            raise RuntimeError("mongo down")
        return False

    touch_env.bulk_touch_file_records = _fail_like_the_real_callee
    public_hash = "9" * cf.PUBLIC_HASH_LENGTH
    rounds = cf._TOUCH_BUFFER_MAX + 20

    for _ in range(rounds):
        cf.schedule_touch_file_record(_record(public_hash))
        await cf._bulk_flush()

    assert len(cf._pending_touches) == 1, (
        f"the same file grew into {len(cf._pending_touches)} buffered rows -- a "
        "failing flush is multiplying deltas instead of merging them"
    )
    assert cf._pending_touches[public_hash][2] == rounds, (
        f"the merged delta is {cf._pending_touches[public_hash][2]}, expected one "
        f"per schedule call ({rounds})"
    )
