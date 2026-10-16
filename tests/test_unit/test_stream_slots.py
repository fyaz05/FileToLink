"""A stalled reader must not hold a stream slot forever.

work_loads[client_id] is the admission counter. It is incremented on every
delivery request and decremented in the body generator's `finally`. A client
that opens a connection, requests a Range, and then stops reading fills the
transport's write buffer, suspends the generator at `yield`, and never unwinds
-- so the slot leaks permanently. EXECUTOR_WORKERS of those and the client
503s forever with no in-flight request to account for it.

These tests drive the real generator through a real consumer that stops
reading, because the whole point is what happens while the generator is
suspended at a yield -- something a mock cannot show.
"""

import asyncio
import contextlib
import types

import pytest

from Thunder.bot import work_loads
from Thunder.server import stream_routes as sr

pytestmark = pytest.mark.unit


_PATTERN = bytes(range(256))


def _bytes_at(start: int, size: int) -> bytes:
    """A chunk whose byte values encode its absolute position (mod 256).

    Cheap (one repeat-and-slice) but position-sensitive: a trim that is skipped,
    or applied to the wrong chunk, shifts the content and shows up in the bytes
    rather than only in the length.
    """
    off = start % 256
    blob = _PATTERN * (size // 256 + 2)
    return blob[off : off + size]


class _StalledStreamer:
    """Yields data forever. The consumer decides how much it takes.

    Models the REAL ByteStreamer.stream_file contract, which is easy to get
    wrong and which this fake previously violated:

        chunk_offset = offset // CHUNK_SIZE          # seek to a chunk BOUNDARY
        chunk_limit = ceil(limit / CHUNK_SIZE) + 1  # in CHUNKS, not bytes

    The real streamer returns whole CHUNK_SIZE pieces starting at the aligned
    offset, and the caller trims the leading ``offset % CHUNK_SIZE`` bytes. The
    old version ignored ``offset`` entirely and yielded exactly ``limit`` bytes,
    which happened to work for every case here because they all used start=0 --
    so the trim path was never exercised, and adding a Range case would have
    silently delivered wrong bytes rather than failing.

    It reads sr.CHUNK_SIZE rather than capturing it, so a test that patches the
    route's chunk size moves both sides together, exactly as custom_dl.CHUNK_SIZE
    does in production.
    """

    def __init__(self):
        self.started = 0
        self.pulled = 0
        self.calls: list[tuple[int, int]] = []

    async def stream_file(self, media_ref, offset=0, limit=0):
        self.started += 1
        self.calls.append((offset, limit))
        chunk_size = sr.CHUNK_SIZE
        chunk_offset = (offset // chunk_size) * chunk_size
        chunk_limit = ((limit + chunk_size - 1) // chunk_size) + 1 if limit > 0 else 0
        for i in range(chunk_limit or 1):
            start = chunk_offset + i * chunk_size
            self.pulled += 1
            yield _bytes_at(start, chunk_size)


async def _drain(body):
    """Consume a body generator the way aiohttp does: keep pulling."""
    async for _ in body:
        pass


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    """Shrink the route's chunk size for the whole module.

    Production is 1 MiB. Building a real 1 MiB position-encoded chunk per test
    would cost more than the tests themselves, and the alignment arithmetic is
    identical at any size -- what matters is that start is not a multiple of it.
    Both sides read sr.CHUNK_SIZE, so this moves them together.
    """
    monkeypatch.setattr(sr, "CHUNK_SIZE", 64)


@pytest.fixture
def client_slot(monkeypatch):
    work_loads[0] = 0
    yield
    work_loads[0] = 0


def _request(range_header=""):
    headers = {"Range": range_header} if range_header else {}
    return types.SimpleNamespace(method="GET", headers=headers, query={})


def _info(size):
    return {"file_size": size, "file_name": "v.mp4", "mime_type": "video/mp4"}


def _body_for(streamer, content_length, client_id=0, deadline=None, start=0):
    """Drive the real body builder with a slot already taken.

    Uses the production release helper rather than a local copy: an earlier
    version re-implemented the idempotency guard here, so making the real one
    non-idempotent left every test green.
    """
    work_loads[client_id] += 1
    release_slot = sr._slot_release(client_id)
    if deadline is None:
        deadline = sr._stream_deadline_seconds(content_length)
    return sr._make_body(streamer, 1, start, content_length, release_slot, deadline)


def test_the_release_helper_is_idempotent():
    """Both the generator's finally and the watchdog can release; a second
    release drives work_loads negative and makes the limiter think there is
    spare capacity."""
    work_loads[0] = 5
    release = sr._slot_release(0)

    release()
    assert work_loads[0] == 4
    release()
    release()
    assert work_loads[0] == 4, f"the slot was released more than once ({work_loads[0]})"


# --- the range-alignment trim ---------------------------------------------
#
# ByteStreamer.stream_file(offset=N) seeks to floor(N / CHUNK_SIZE) * CHUNK_SIZE
# and yields whole chunks from there, so the body generator must drop the
# leading N % CHUNK_SIZE bytes itself. Nothing pinned that: every case in this
# file used start=0, where the trim is a no-op, and the fake ignored offset --
# so the arithmetic could have been deleted outright and the suite stayed green.


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start", "length"),
    [
        (0, 100),  # aligned: no trim at all
        (1, 100),  # one byte of trim
        (10, 10),  # trim longer than the requested span
        (63, 1),  # last byte of the first chunk
        (64, 64),  # exactly chunk-aligned start
        (65, 130),  # spans three chunks from a misaligned start
        (200, 10),  # start deep past the first chunk
    ],
)
async def test_a_range_gets_exactly_the_requested_bytes(client_slot, start, length):
    body = _body_for(_StalledStreamer(), length, start=start)
    delivered = b"".join([chunk async for chunk in body])

    assert len(delivered) == length, f"start={start} length={length}: got {len(delivered)} bytes"
    assert delivered == _bytes_at(start, length), (
        f"start={start}: the wrong bytes were delivered -- the offset % CHUNK_SIZE trim is wrong or missing"
    )
    assert work_loads[0] == 0, "the slot was not released"


@pytest.mark.unit
async def test_the_streamer_is_asked_for_the_aligned_position(client_slot):
    """The route passes the raw start; the STREAMER aligns it, and the route
    trims. If either side moved the other way the bytes would be wrong."""
    streamer = _StalledStreamer()
    body = _body_for(streamer, 100, start=70)

    async for _ in body:
        pass

    assert streamer.calls == [(70, 100)], (
        f"stream_file was called with {streamer.calls}; the route must pass the raw "
        "offset and let the streamer align it to a chunk boundary"
    )


@pytest.mark.unit
async def test_the_fake_is_position_sensitive(client_slot):
    """Guards the tests above against becoming vacuous: if the fake stopped
    encoding position, a broken trim would pass."""
    body = _body_for(_StalledStreamer(), 10, start=10)
    delivered = b"".join([chunk async for chunk in body])

    assert delivered != _bytes_at(0, 10), (
        "the fake no longer distinguishes positions -- a missing trim would go unnoticed"
    )


# --- the deadline itself ---------------------------------------------------


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, sr._STREAM_MIN_SECONDS),  # unknown size: the floor
        (1024, sr._STREAM_MIN_SECONDS),  # tiny: still the floor
        (sr._STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND * 600, 600.0),  # rate-limited
        (sr._STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND * 10**9, sr._STREAM_MAX_SECONDS),  # capped
    ],
)
def test_the_deadline_scales_with_the_transfer(size, expected):
    assert sr._stream_deadline_seconds(size) == pytest.approx(expected)


def test_the_deadline_is_always_positive_and_bounded():
    for size in (-1, 0, 1, 10**12):
        d = sr._stream_deadline_seconds(size)
        assert 0 < d <= sr._STREAM_MAX_SECONDS


def test_the_deadline_is_finite_for_an_enormous_file():
    """A file larger than any disk still gets a deadline."""
    assert sr._stream_deadline_seconds(10**18) == sr._STREAM_MAX_SECONDS


# --- a stalled reader ------------------------------------------------------


async def test_a_stalled_consumer_gets_its_slot_back(monkeypatch, client_slot):
    """The regression, end to end: take one chunk, then stop reading.

    The generator is left suspended at `yield`, which is exactly the state a
    slowloris-style reader produces. Without the deadline the slot is never
    returned.
    """
    monkeypatch.setattr(sr, "_STREAM_MIN_SECONDS", 0.2)
    monkeypatch.setattr(sr, "_STREAM_MAX_SECONDS", 0.2)
    monkeypatch.setattr(sr, "_STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND", 1)

    streamer = _StalledStreamer()

    body = _body_for(streamer, 10**6)
    assert work_loads[0] == 1, "the slot was not taken before the test began"

    # take exactly one chunk, then go quiet -- this is the client stopping
    # to read. The generator is now suspended at a yield with nobody waiting
    # on it, which is the state it can never unwind from on its own.
    await anext(body)
    assert work_loads[0] == 1, "the slot was released while the stream was still live"

    # let the deadline pass without touching the body again
    await asyncio.sleep(0.6)

    assert work_loads[0] == 0, (
        "a stalled reader kept its stream slot -- EXECUTOR_WORKERS of these and "
        "the client is 503'd forever"
    )
    with pytest.raises(StopAsyncIteration):
        await anext(body)


async def test_a_slot_is_never_released_twice(monkeypatch, client_slot):
    """Double release drives the counter negative, which makes the limiter
    believe there is spare capacity -- worse than the leak being fixed."""
    monkeypatch.setattr(sr, "_STREAM_MIN_SECONDS", 5.0)
    monkeypatch.setattr(sr, "_STREAM_MAX_SECONDS", 5.0)
    monkeypatch.setattr(sr, "_STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND", 1)

    body = _body_for(_StalledStreamer(), 4096)

    await anext(body)
    # normal completion: the generator's own finally releases
    remaining = [chunk async for chunk in body]

    assert len(remaining) >= 1
    assert work_loads[0] == 0, "a completed stream left its slot held"
    # and closing again must not double-decrement
    await body.aclose()
    assert work_loads[0] == 0, f"the slot counter went to {work_loads[0]} -- released twice"


async def test_a_healthy_stream_releases_exactly_once(client_slot):
    body = _body_for(_StalledStreamer(), 4096)
    chunks = [c async for c in body]
    assert len(chunks) >= 1
    assert work_loads[0] == 0


async def test_head_releases_the_slot_without_a_body(client_slot):
    request = types.SimpleNamespace(method="HEAD", headers={}, query={})
    work_loads[0] = 1
    await sr._serve_media_response(
        request,
        file_info=_info(4096),
        streamer=_StalledStreamer(),
        client_id=0,
        media_ref=1,
        release_slot=sr._slot_release(0),
    )
    assert work_loads[0] == 0, "a HEAD request leaked its slot"


# --- one release closure for the whole request ------------------------------
#
# The ladder, the generator's finally and the watchdog can all be first to
# return a slot. Three separate decrements were three chances to go negative,
# and a failure between "watchdog created" and "Response returned" genuinely
# hit two of them at once.


async def test_the_ladder_hands_the_stream_the_same_release(client_slot):
    """The ladder, the generator's finally and the watchdog can all be first to
    return a slot, so there must be ONE closure, not three decrements.

    Decisive rather than arithmetic: if _serve_media_response minted its own
    closure, the ladder's would still be unused, and calling it after HEAD would
    take the counter to -1.
    """
    work_loads[0] = 1  # admission already took the slot

    async with sr._admission_ladder(0, "probe") as release:
        await sr._serve_media_response(
            types.SimpleNamespace(method="HEAD", headers={}, query={}),
            file_info=_info(4096),
            streamer=_StalledStreamer(),
            client_id=0,
            media_ref=1,
            release_slot=release,
        )
        assert work_loads[0] == 0, "HEAD should have returned the slot"

        # the ladder's own release on the way out must now be a no-op
        release()

    assert work_loads[0] == 0, (
        f"work_loads is {work_loads[0]} -- _serve_media_response used its own "
        "release closure instead of the ladder's, so the slot is counted twice"
    )


async def test_the_watchdog_does_not_release_a_slot_the_ladder_already_returned(monkeypatch):
    """The failure window is between _make_body (watchdog created, holding the
    release) and the Response returning. The ladder releases on the way out and
    the watchdog releases at its deadline -- with two closures that is -1, which
    makes select_optimal_client believe there is spare capacity.

    Driven through _serve_media_response, not _make_body directly, because the
    HEAD path returns before _serve_media_response would touch the passed-in
    closure -- so a HEAD-only test cannot see a second closure being minted.
    The streaming path is where the swap happens.
    """
    monkeypatch.setattr(sr, "_STREAM_MIN_SECONDS", 0.15)
    monkeypatch.setattr(sr, "_STREAM_MAX_SECONDS", 0.15)
    monkeypatch.setattr(sr, "_STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND", 1)

    work_loads[0] = 1
    release = sr._slot_release(0)
    response = await sr._serve_media_response(
        types.SimpleNamespace(method="GET", headers={}, query={}),
        file_info=_info(10**7),
        streamer=_StalledStreamer(),
        client_id=0,
        media_ref=1,
        release_slot=release,
    )
    assert work_loads[0] == 1, "the slot should still be held: nobody consumed the body"

    # the request fails before/while the body is delivered, so the ladder's
    # except-path returns the slot
    release()
    assert work_loads[0] == 0, "the ladder did not release"

    await asyncio.sleep(0.4)  # the watchdog's deadline passes

    assert work_loads[0] == 0, (
        f"work_loads is {work_loads[0]} -- the watchdog released a slot the "
        "ladder had already returned, so _serve_media_response must have minted "
        "its own closure instead of reusing the one it was given"
    )
    # the response wraps the generator in a payload; closing it tears the
    # transfer down the way aiohttp does on a client disconnect
    response.body.close()


async def test_both_routes_thread_the_ladder_closure_into_the_response():
    """Structural, because the property is about a call site, not arithmetic.

    If a route forgets `release_slot=`, _serve_media_response mints a fresh
    closure and the ladder's release stops being idempotent with the watchdog's
    -- the exact double-decrement above, invisible until a transfer stalls at
    just the wrong moment.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sr))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_serve_media_response"
    ]
    assert len(calls) == 2, f"expected 2 call sites, found {len(calls)} -- the scan is stale"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "release_slot" in kwargs, (
            f"line {call.lineno}: this route does not thread the ladder's "
            "release closure into _serve_media_response"
        )


# --- the watchdog ----------------------------------------------------------


async def test_the_watchdog_releases_and_closes_when_the_deadline_passes():
    released = []
    closed = []

    async def body():
        try:
            while True:
                yield b"x"
        finally:
            closed.append(1)

    gen = body()
    await anext(gen)  # a generator that never started cannot be closed
    task = asyncio.ensure_future(
        sr._reclaim_slot_when_stalled(asyncio.Event(), 0.15, lambda: released.append(1), gen)
    )

    await asyncio.sleep(0.4)
    assert released, "the watchdog let the deadline pass without reclaiming the slot"
    assert closed, "the stalled generator was left open -- the vault fetch keeps running"
    task.cancel()


async def test_the_watchdog_releases_nothing_when_the_stream_completes():
    released = []
    completed = asyncio.Event()

    async def body():
        yield b"x"

    gen = body()
    task = asyncio.ensure_future(
        sr._reclaim_slot_when_stalled(completed, 5.0, lambda: released.append(1), gen)
    )
    await anext(gen)
    completed.set()
    await asyncio.sleep(0.05)
    task.cancel()

    assert not released, "the watchdog reclaimed a slot from a stream that finished"


async def test_cancelling_the_watchdog_reclaims_nothing():
    released = []
    completed = asyncio.Event()

    async def body():
        yield b"x"

    gen = body()
    task = asyncio.ensure_future(
        sr._reclaim_slot_when_stalled(completed, 30.0, lambda: released.append(1), gen)
    )
    await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.02)

    assert not released, (
        "cancelling the watchdog released the slot twice -- the generator's own "
        "cleanup already did it"
    )


async def test_the_watchdog_does_not_close_a_generator_a_consumer_is_pulling(monkeypatch):
    """`aclose()` only works on a generator suspended at a `yield`.

    While a consumer is actively awaiting the next chunk -- the NORMAL state, and
    exactly what a slow upstream looks like -- the generator is suspended inside
    `await streamer.stream_file(...)` and aclose() raises
    "RuntimeError: asynchronous generator is already running". An earlier version
    wrapped it in suppress(RuntimeError), which turned a real, reportable failure
    into silence while claiming the upstream fetch had been stopped.
    """
    monkeypatch.setattr(sr, "_STREAM_MIN_SECONDS", 0.2)
    monkeypatch.setattr(sr, "_STREAM_MAX_SECONDS", 0.2)
    monkeypatch.setattr(sr, "_STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND", 1)

    upstream_closed = []

    class SlowUpstream:
        async def stream_file(self, media_ref, offset=0, limit=0):
            try:
                while True:
                    yield b"x" * 64
                    await asyncio.sleep(5)  # long enough that the deadline lands here
            finally:
                upstream_closed.append(1)

    body = _body_for(SlowUpstream(), 10**7)
    consumer = asyncio.ensure_future(_drain(body))
    await asyncio.sleep(0.1)
    assert body.ag_running, "the consumer should be mid-__anext__, not parked at a yield"

    # Hold the watchdog so its outcome is observable. Removing the ag_running
    # guard makes aclose() raise RuntimeError, and the effects the other
    # assertions look at (upstream not closed, slot reclaimed) are IDENTICAL
    # either way -- release() runs first. The only difference is whether the
    # watchdog survives, which only surfaces by awaiting it. A task's exception
    # reaches the loop's handler at GC time, so checking that would be racy.
    watchdog = next(
        (t for t in asyncio.all_tasks() if t.get_name() == "stream_slot_watchdog"),
        None,
    )
    assert watchdog is not None, "no watchdog task was created"

    try:
        await asyncio.wait_for(asyncio.shield(watchdog), timeout=2.0)
    except RuntimeError as e:
        pytest.fail(
            f"the watchdog died with {e!r} -- aclose() was called on a generator "
            "a consumer is already pulling. The guard is gone."
        )
    except TimeoutError:
        pytest.fail("the watchdog never finished; its deadline did not fire")

    assert work_loads[0] == 0, "the slot must be reclaimed even when we cannot close"
    assert not upstream_closed, (
        "the upstream was closed -- aclose() cannot do that while a consumer is "
        "pulling, so either the guard is gone or this test is not in the state "
        "it claims to be"
    )
    assert not consumer.done(), "the consumer should still own the generator"
    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer


async def test_the_watchdog_reclaims_while_a_consumer_is_mid_fetch(monkeypatch):
    """The slot is the thing that must always come back. Even when the
    generator cannot be closed, the counter must not stay held."""
    monkeypatch.setattr(sr, "_STREAM_MIN_SECONDS", 0.15)
    monkeypatch.setattr(sr, "_STREAM_MAX_SECONDS", 0.15)
    monkeypatch.setattr(sr, "_STREAM_MIN_THROUGHPUT_BYTES_PER_SECOND", 1)

    class SlowUpstream:
        async def stream_file(self, media_ref, offset=0, limit=0):
            while True:
                yield b"x" * 64
                await asyncio.sleep(5)

    body = _body_for(SlowUpstream(), 10**7)
    consumer = asyncio.ensure_future(_drain(body))
    await asyncio.sleep(0.05)
    await asyncio.sleep(0.4)

    assert work_loads[0] == 0, (
        "a stalled reader held its slot because the generator was mid-fetch -- "
        "the reclaim must not depend on being able to close it"
    )
    consumer.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await consumer


async def test_the_watchdog_does_not_outlive_a_healthy_stream():
    """A finished stream must not leave a task waiting out the full deadline."""
    body = _body_for(_StalledStreamer(), 4096)
    [c async for c in body]

    await asyncio.sleep(0.05)
    leftovers = [
        t
        for t in asyncio.all_tasks()
        if t.get_name().startswith("stream_slot_watchdog") and not t.done()
    ]
    assert not leftovers, f"watchdog tasks leaked: {[t.get_name() for t in leftovers]}"
