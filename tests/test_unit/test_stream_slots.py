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
import types

import pytest

from Thunder.bot import work_loads
from Thunder.server import stream_routes as sr

pytestmark = pytest.mark.unit


class _StalledStreamer:
    """Yields data forever. The consumer decides how much it takes."""

    def __init__(self):
        self.started = 0

    async def stream_file(self, media_ref, offset=0, limit=0):
        self.started += 1
        chunk = b"x" * 1024
        sent = 0
        while sent < limit:
            yield chunk
            sent += len(chunk)


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


def _body_for(streamer, content_length, client_id=0, deadline=None):
    """Drive the real body builder with a slot already taken.

    Uses the production release helper rather than a local copy: an earlier
    version re-implemented the idempotency guard here, so making the real one
    non-idempotent left every test green.
    """
    work_loads[client_id] += 1
    release_slot = sr._slot_release(client_id)
    if deadline is None:
        deadline = sr._stream_deadline_seconds(content_length)
    return sr._make_body(streamer, 1, 0, content_length, release_slot, deadline)


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
        request, file_info=_info(4096), streamer=_StalledStreamer(), client_id=0, media_ref=1
    )
    assert work_loads[0] == 0, "a HEAD request leaked its slot"


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
