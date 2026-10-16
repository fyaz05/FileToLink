"""Ingest-claim protocol: acquire / release / steal-expired / owner-check,
plus the four functions around it that previously had ZERO coverage and are
where the real defects lived:

- get_or_create_canonical_file      (the ingest decision itself)
- file_ingest_lock                  (held a sleeper once: see below)
- _wait_for_other_worker_canonical_record
- update_cached_file_id

Two tests in this file used to be arithmetic over module constants
(`10*90 >= 900`), which cannot see behaviour: shrinking the real window to 1s
left them green. The timing contract is now asserted against the constants the
loop actually uses, and -- more importantly -- the lock is pinned by a test
that measures what a contended caller waits for.
"""

from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from Thunder.utils.database import Database


class _FakeLocksCol:
    """Stand-in for file_ingest_locks_col that HONOURS the filter it is given.

    The previous version re-derived liveness from its own clock and ignored the
    query entirely, so `acquire_file_ingest_claim`'s $or could be reduced to
    `{"_id": ...}` -- stealing live claims, i.e. two workers copying the same
    file -- and every test stayed green. The predicate IS the security property,
    so the fake must evaluate it the way Mongo does.
    """

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.seen_filters: list[dict] = []

    async def insert_one(self, doc: dict):
        if doc["_id"] in self.store:
            raise DuplicateKeyError("dup")
        self.store[doc["_id"]] = dict(doc)

    def _matches(self, current: dict, flt: dict) -> bool:
        from datetime import datetime

        for key, want in flt.items():
            if key == "$or":
                if not any(self._matches(current, clause) for clause in want):
                    return False
            elif key == "$and":
                if not all(self._matches(current, clause) for clause in want):
                    return False
            elif isinstance(want, dict):
                # field-level operators: {"expires_at": {"$lte": now}}
                for op, operand in want.items():
                    got = current.get(key)
                    if op == "$lte":
                        if got is None or got > datetime.now(got.tzinfo):
                            return False
                        if isinstance(operand, datetime) and got > operand:
                            return False
                    elif op == "$gt":
                        if got is None or got <= datetime.now(got.tzinfo):
                            return False
                        if isinstance(operand, datetime) and got <= operand:
                            return False
                    elif op == "$exists":
                        if (key in current) is not bool(operand):
                            return False
                    else:
                        return False  # an operator this fake cannot model
            elif current.get(key) != want:
                return False
        return True

    async def find_one_and_update(self, flt: dict, update: dict, **_kwargs):
        self.seen_filters.append(dict(flt))
        current = self.store.get(flt.get("_id"))
        if current is None or not self._matches(current, flt):
            return None
        old = dict(current)
        current.update(update["$set"])
        return old

    async def delete_one(self, flt: dict):
        self.seen_filters.append(dict(flt))
        current = self.store.get(flt.get("_id"))
        if current is not None and self._matches(current, flt):
            del self.store[flt["_id"]]
            return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def _make_claim_db() -> Database:
    db = Database.__new__(Database)
    db.file_ingest_locks_col = _FakeLocksCol()  # type: ignore[assignment]
    return db


@pytest.mark.unit
async def test_acquire_release_cycle():
    db = _make_claim_db()
    owner = await db.acquire_file_ingest_claim("file-1")
    assert owner  # opaque owner token
    # live claim blocks a second worker
    assert await db.acquire_file_ingest_claim("file-1") is None
    # owner-checked release frees it
    assert await db.release_file_ingest_claim("file-1", owner) is True
    assert await db.release_file_ingest_claim("file-1", owner) is False
    # free again: a new worker can claim
    assert await db.acquire_file_ingest_claim("file-1")


@pytest.mark.unit
async def test_expired_claim_is_stolen():
    db = _make_claim_db()
    owner = await db.acquire_file_ingest_claim("file-2", ttl_seconds=-1)  # already expired
    thief = await db.acquire_file_ingest_claim("file-2")
    assert thief and thief != owner
    # the old owner's release must not delete the new worker's claim
    assert await db.release_file_ingest_claim("file-2", owner) is False
    assert await db.release_file_ingest_claim("file-2", thief) is True


@pytest.mark.unit
async def test_the_steal_is_gated_on_expiry_in_the_query_itself():
    """The $or is the security property: without it a second worker takes a
    LIVE claim and both copy the file. Pin the filter, not just the outcome --
    the previous fake re-derived liveness from its own clock, so deleting the
    $or left every test green."""
    db = _make_claim_db()
    assert await db.acquire_file_ingest_claim("file-3", ttl_seconds=900), "setup: no claim"

    db.file_ingest_locks_col.seen_filters.clear()
    assert await db.acquire_file_ingest_claim("file-3") is None, "a live claim was stolen"

    steal_filters = [
        f
        for f in db.file_ingest_locks_col.seen_filters
        if "$or" in f or any("$lte" in clause for clause in f.get("$or", []))
    ]
    assert steal_filters, (
        "acquire_file_ingest_claim did not query with an expiry condition -- "
        "a live claim can be taken and two workers copy the same file"
    )
    for flt in db.file_ingest_locks_col.seen_filters:
        assert flt.get("_id") == "file-3", "the claim is scoped by file"


@pytest.mark.unit
async def test_release_is_owner_scoped_in_the_query():
    """A stale owner's release must not delete the thief's claim. Pin it in the
    filter, not only in the observed result."""
    db = _make_claim_db()
    owner = await db.acquire_file_ingest_claim("file-4", ttl_seconds=-1)
    thief = await db.acquire_file_ingest_claim("file-4")

    db.file_ingest_locks_col.seen_filters.clear()
    await db.release_file_ingest_claim("file-4", owner)
    assert db.file_ingest_locks_col.seen_filters, "release sent no filter"

    delete_filters = [
        f
        for f in db.file_ingest_locks_col.seen_filters
        if f.get("_id") == "file-4" and f.get("owner") == owner
    ]
    assert delete_filters, "the release filter is not owner-scoped"
    assert await db.release_file_ingest_claim("file-4", thief) is True


# --- the timing contract ---------------------------------------------------
#
# These replace `10*90 >= 900`, which asserted nothing about the loop: shrinking
# the real wait window to 1s left it green. Note the direction has FLIPPED. The
# old comment demanded that the retry budget cover the claim TTL, which meant
# occupying an executor worker for up to 15 minutes. That is backwards -- a
# requeued request comes back in seconds and finds the record the other worker
# produced. The budget is now a short poll that raises IngestContended.


@pytest.mark.unit
def test_the_wait_loop_uses_the_constant_the_budget_is_measured_in():
    """Pin the wiring, not the arithmetic: the old tests read the constants while
    the loop used `loop.time() + _INGEST_CLAIM_WAIT_SECONDS` -- a retune of the
    loop alone went unnoticed."""
    import inspect

    from Thunder.utils import canonical_files as cf

    src = inspect.getsource(cf._wait_for_other_worker_canonical_record)
    assert "_INGEST_CLAIM_WAIT_SECONDS" in src, (
        "the wait loop no longer uses _INGEST_CLAIM_WAIT_SECONDS, so the budget "
        "arithmetic no longer describes it"
    )
    assert "_INGEST_CLAIM_POLL_SECONDS" in src, "the poll interval is now hardcoded"


@pytest.mark.unit
def test_claim_ttl_outlives_a_slow_copy():
    """The claim must outlast a plausible large copy, or a second worker steals
    it mid-upload and both copy the file."""
    from Thunder.utils import canonical_files as cf

    assert cf._INGEST_CLAIM_TTL_SECONDS >= 300, "a multi-GB copy can outrun this TTL"


@pytest.mark.unit
def test_the_poll_interval_cannot_flood_telegram():
    """At 0.5s each poll cost a Mongo find plus a get_messages when a stale
    record existed: ~1.8k RPCs per stuck request, which can raise the very
    FloodWait the poll is waiting out."""
    from Thunder.utils import canonical_files as cf

    assert cf._INGEST_CLAIM_POLL_SECONDS >= 1.0, (
        "polling faster than once a second turns a wait into an RPC flood"
    )
    polls_per_window = cf._INGEST_CLAIM_WAIT_SECONDS / cf._INGEST_CLAIM_POLL_SECONDS
    assert polls_per_window <= 90, f"{polls_per_window:.0f} polls per wait window is too many"


# --- file_ingest_lock: the liveness bug -----------------------------------
#
# `async with file_ingest_lock(...)` used to wrap the ENTIRE retry loop,
# including _wait_for_other_worker_canonical_record's up-to-90s sleep per
# attempt. One contended file therefore held an executor worker for up to
# _MAX_INGEST_RETRIES * _INGEST_CLAIM_WAIT_SECONDS, and the pool has only
# EXECUTOR_WORKERS of them: five requests naming the same file wedged the whole
# ingest queue, with no cross-process claim involved at all.


@pytest.mark.unit
async def test_the_lock_is_released_before_the_wait_for_another_worker(monkeypatch):
    """The sleeper must run OUTSIDE the lock."""
    import contextlib

    from Thunder.utils import canonical_files as cf

    real_lock = cf.file_ingest_lock
    state = {"held": 0}

    @contextlib.asynccontextmanager
    async def tracking_lock(key):
        async with real_lock(key):
            state["held"] += 1
            try:
                yield
            finally:
                state["held"] -= 1

    monkeypatch.setattr(cf, "file_ingest_lock", tracking_lock)
    monkeypatch.setattr(cf, "_MAX_INGEST_RETRIES", 3)
    monkeypatch.setattr(cf, "_INGEST_CLAIM_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(cf.asyncio, "sleep", _no_sleep)

    held_during_wait = []

    async def wait_probe(file_unique_id, client):
        held_during_wait.append(state["held"] > 0)
        return None

    monkeypatch.setattr(cf, "_wait_for_other_worker_canonical_record", wait_probe)
    monkeypatch.setattr(cf, "_get_reusable_canonical_record", _never_reusable)
    monkeypatch.setattr(cf.db, "acquire_file_ingest_claim", _claim_denied)
    monkeypatch.setattr(cf.db, "is_file_ingest_claim_active", _always_active)

    with pytest.raises(cf.IngestContended):
        await cf.get_or_create_canonical_file(_msg(), _never_copies, client=None)

    assert held_during_wait, "the probe never ran -- the test is not exercising anything"
    assert not any(held_during_wait), (
        "the wait for another worker runs while file_ingest_lock is held -- "
        f"{sum(held_during_wait)} of {len(held_during_wait)} waits held the "
        "lock, which is how one contended file used to park an executor worker "
        "for the full retry budget"
    )


@pytest.mark.unit
async def test_contention_escapes_the_handler(monkeypatch):
    """The signal must actually reach the rate limiter. Both stream.py handlers
    have a broad `except Exception` that would log it and move on, leaving the
    user with nothing -- and the worker still held."""
    from Thunder.bot.plugins import stream
    from Thunder.utils import canonical_files as cf

    monkeypatch.setattr(cf, "_MAX_INGEST_RETRIES", 1)
    monkeypatch.setattr(cf, "_INGEST_CLAIM_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(cf.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(cf, "_get_reusable_canonical_record", _never_reusable)
    monkeypatch.setattr(cf.db, "acquire_file_ingest_claim", _claim_denied)
    monkeypatch.setattr(cf.db, "is_file_ingest_claim_active", _always_active)

    async def wait_probe(file_unique_id, client):
        return None

    monkeypatch.setattr(cf, "_wait_for_other_worker_canonical_record", wait_probe)

    with pytest.raises(cf.IngestContended):
        await stream.process_single(None, _msg(), _msg(), None, False)

    # and the limiter must be the thing that turns it into a requeue
    from Thunder.utils import rate_limiter as rl

    assert rl.MAX_CONTENDED_ATTEMPTS < 10**6, "contention requeues must stay bounded"


@pytest.mark.unit
async def test_a_denied_claim_does_not_sleep_forever(monkeypatch):
    """A held claim must not park the caller until the TTL. It raises
    IngestContended so the limiter can requeue and free the worker."""
    from Thunder.utils import canonical_files as cf

    monkeypatch.setattr(cf, "_MAX_INGEST_RETRIES", 1)
    monkeypatch.setattr(cf, "_INGEST_CLAIM_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(cf.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(cf, "_get_reusable_canonical_record", _never_reusable)
    monkeypatch.setattr(cf.db, "acquire_file_ingest_claim", _claim_denied)
    monkeypatch.setattr(cf.db, "is_file_ingest_claim_active", _always_active)

    async def wait_probe(file_unique_id, client):
        return None

    monkeypatch.setattr(cf, "_wait_for_other_worker_canonical_record", wait_probe)

    with pytest.raises(cf.IngestContended):
        await cf.get_or_create_canonical_file(_msg(), _never_copies, client=None)


@pytest.mark.unit
def test_every_caller_lets_contention_through():
    """Structural, and the gap this found: my behavioural test drove
    process_single, so the re-raise in _actual_channel_receive_handler -- the
    one on the channel-post path -- was unpinned and could be deleted silently.

    Every function that calls get_or_create_canonical_file must have an
    `except IngestContended: raise` clause. Both stream.py handlers have a broad
    `except Exception` that would otherwise log it and hand the user nothing.
    """
    import ast
    import inspect

    from Thunder.bot.plugins import stream

    tree = ast.parse(inspect.getsource(stream))
    callers = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = any(
            isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "get_or_create_canonical_file"
            for n in ast.walk(fn)
        )
        if calls:
            callers.append(fn.name)

    assert callers, "the source scan found no caller -- the test is not examining anything"

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in callers:
            continue
        reraises = False
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                names = (
                    {t.id for t in ast.walk(handler.type) if isinstance(t, ast.Name)}
                    if handler.type
                    else set()
                )
                if "IngestContended" in names and any(
                    isinstance(b, ast.Raise) and b.exc is None for b in ast.walk(handler)
                ):
                    reraises = True
        assert reraises, (
            f"{fn.name}() calls get_or_create_canonical_file but has no "
            "`except IngestContended: raise` -- its broad `except Exception` will "
            "swallow the contention signal and the user gets nothing"
        )


@pytest.mark.unit
async def test_contention_carries_the_file_and_a_delay():
    from Thunder.utils import canonical_files as cf

    exc = cf.IngestContended("file-abc", 20.0)
    assert exc.file_unique_id == "file-abc"
    assert exc.retry_after == 20.0
    # the shipped delay must be long enough that a requeue does not spin
    assert cf._INGEST_CLAIM_WAIT_SECONDS >= 5.0, (
        f"a requeue after {cf._INGEST_CLAIM_WAIT_SECONDS}s will just re-contend in a tight loop"
    )


@pytest.mark.unit
async def test_the_retry_budget_no_longer_waits_out_the_claim_ttl():
    """The old invariant -- retries x wait >= TTL -- is backwards: it required
    occupying a worker for the whole TTL. The budget is now a short poll."""
    from Thunder.utils import canonical_files as cf

    worst_case = cf._MAX_INGEST_RETRIES * cf._INGEST_CLAIM_WAIT_SECONDS
    assert worst_case < cf._INGEST_CLAIM_TTL_SECONDS, (
        f"the handler can still wait {worst_case}s, which is most of the "
        f"{cf._INGEST_CLAIM_TTL_SECONDS}s claim TTL -- the worker is parked again"
    )
    assert worst_case <= 120, (
        f"a caller can still be held for {worst_case}s; the pool has only EXECUTOR_WORKERS of these"
    )


@pytest.mark.unit
async def test_the_wait_window_is_shorter_than_the_claim_ttl():
    """One window must not be able to hold a worker for the whole TTL either."""
    from Thunder.utils import canonical_files as cf

    assert cf._INGEST_CLAIM_WAIT_SECONDS < cf._INGEST_CLAIM_TTL_SECONDS


@pytest.mark.unit
async def test_the_lock_is_still_held_while_the_copy_runs(monkeypatch):
    """Serialising the copy is CORRECT -- only one worker may upload the file.
    This pins that the lock is taken for the claim+copy critical section, so the
    fix above cannot be 'regressed' into releasing it around the copy."""
    import contextlib

    from Thunder.utils import canonical_files as cf

    real_lock = cf.file_ingest_lock
    depth = {"now": 0, "max": 0}

    @contextlib.asynccontextmanager
    async def tracking_lock(key):
        async with real_lock(key):
            depth["now"] += 1
            depth["max"] = max(depth["max"], depth["now"])
            try:
                yield
            finally:
                depth["now"] -= 1

    monkeypatch.setattr(cf, "file_ingest_lock", tracking_lock)
    monkeypatch.setattr(cf, "_MAX_INGEST_RETRIES", 1)
    monkeypatch.setattr(cf, "_get_reusable_canonical_record", _never_reusable)
    monkeypatch.setattr(cf, "db", _ClaimGrantedDb())
    monkeypatch.setattr(
        cf, "build_file_record", lambda *a, **k: {"public_hash": "a" * cf.PUBLIC_HASH_LENGTH}
    )

    async def copy(message):
        return SimpleNamespace(id=99)

    await cf.get_or_create_canonical_file(_msg(), copy, client=None)

    assert depth["max"] == 1, (
        "the copy ran with no lock held; two workers could upload the same file"
    )
    assert depth["now"] == 0, "the lock was not released"


# --- update_cached_file_id -------------------------------------------------


@pytest.mark.unit
async def test_the_cache_is_only_updated_after_mongo_agrees(monkeypatch):
    """It used to write the cache first, so a failed DB write left the cache
    holding an id Mongo does not know."""
    from Thunder.utils import canonical_files as cf

    record = {"public_hash": "a" * cf.PUBLIC_HASH_LENGTH, "file_id": "old"}
    cache_at_failure = {}

    class _Db:
        async def update_file_id(self, public_hash, file_id, *, raise_on_error=False):
            cache_at_failure["file_id"] = record.get("file_id")
            raise RuntimeError("mongo down")

    monkeypatch.setattr(cf, "db", _Db())

    with pytest.raises(RuntimeError):
        await cf.update_cached_file_id(record, "new-id")

    assert cache_at_failure["file_id"] == "old", (
        "the cache was updated before Mongo, so a failed write leaves a file_id "
        "that no record in the database has"
    )
    assert record["file_id"] == "old", "the caller's record was mutated despite the failure"


@pytest.mark.unit
async def test_a_successful_write_updates_both(monkeypatch):
    from Thunder.utils import canonical_files as cf

    record = {"public_hash": "a" * cf.PUBLIC_HASH_LENGTH, "file_id": "old"}
    written = {}

    class _Db:
        async def update_file_id(self, public_hash, file_id, *, raise_on_error=False):
            written["file_id"] = file_id
            return True

    monkeypatch.setattr(cf, "db", _Db())

    await cf.update_cached_file_id(record, "new-id")

    assert written["file_id"] == "new-id"
    assert record["file_id"] == "new-id", "the cache was not updated after a successful write"


@pytest.mark.unit
async def test_no_op_arguments_are_ignored(monkeypatch):
    from Thunder.utils import canonical_files as cf

    class _Db:
        async def update_file_id(self, *a, **k):
            raise AssertionError("must not write with no hash or no id")

    monkeypatch.setattr(cf, "db", _Db())

    await cf.update_cached_file_id({"file_id": "old"}, "new-id")
    await cf.update_cached_file_id({"public_hash": "h"}, "")


# --- helpers --------------------------------------------------------------


def _msg(file_unique_id="uniq-file-1"):
    """A message carrying media: get_uniqid reads message.<x>.file_unique_id, so a
    media-less fake returns early and every test here passes vacuously."""
    media = SimpleNamespace(file_unique_id=file_unique_id, file_id="fid", file_size=10)
    return SimpleNamespace(
        id=1,
        chat=SimpleNamespace(id=-100123),
        document=media,
        media=media,
        caption="",
    )


async def _never_reusable(file_unique_id, client):
    return None, None


async def _never_copies(message):
    raise AssertionError("copy_media must not run without a claim")


async def _claim_denied(file_unique_id, *, ttl_seconds=None):
    return None


async def _always_active(file_unique_id):
    return True


async def _no_sleep(_s):
    return None


class _ClaimGrantedDb:
    """A claim that always succeeds, so the copy path is reached."""

    async def acquire_file_ingest_claim(self, *_a, **_k):
        return "owner-1"

    async def release_file_ingest_claim(self, *_a, **_k):
        return True

    async def create_file_record(self, record):
        return record

    async def replace_file_record(self, record):
        return record

    async def is_file_ingest_claim_active(self, *_a, **_k):
        return False

    async def bulk_touch_file_records(self, rows, *, raise_on_error: bool = False):
        return True
