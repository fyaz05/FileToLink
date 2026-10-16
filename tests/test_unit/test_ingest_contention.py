"""Ingest contention must requeue, not park a worker.

get_or_create_canonical_file raises IngestContended when another worker holds
the claim for the same file. The rate limiter turns that into a requeue so the
executor worker is freed immediately.

This is the second half of that contract. The first half (the raise) is in
test_canonical_claim.py; what matters here is that the limiter's branch is
wired, bounded, and does not confuse itself with the FloodWait counter.
"""

from types import SimpleNamespace

import pytest

from Thunder.utils import rate_limiter as rl

pytestmark = pytest.mark.unit


@pytest.fixture
def limiter(monkeypatch):
    """The real singleton, configured and emptied -- hand-building a RateLimiter
    drifts from __init__ (that is how _initialization_error went missing once)."""

    lim = rl.rate_limiter
    lim.enabled = True
    lim._initialization_error = False
    lim.max_requests_per_period = 10_000
    lim.max_global_requests_per_minute = 10_000
    lim.rate_limit_period_seconds = 60
    lim.global_rate_limit_enabled = False
    lim.breaker = SimpleNamespace(rate=0.0, allow=lambda: True, retry_after=lambda: 0.0)
    lim.is_owner = lambda user_id: False
    lim.user_requests.clear()
    lim.global_requests.clear()
    lim.request_queue.clear()
    lim.priority_queue.clear()
    lim.notified = []

    async def note(self, request_data):
        self.notified.append(request_data)

    monkeypatch.setattr(lim, "_notify_drop", note.__get__(lim, type(lim)))
    monkeypatch.setattr(lim, "_park_if_all_deferred", lambda: None)
    yield lim
    lim.request_queue.clear()
    lim.priority_queue.clear()
    lim.user_requests.clear()
    lim.notified = []
    lim.is_owner = rl.RateLimiter.is_owner.__get__(lim, type(lim))


async def _drive(limiter, request):
    """Queue one request and let _process_one take it off the deque itself."""
    limiter.request_queue.append(request)
    return await limiter._process_one()


def _deques(limiter):
    from collections import deque

    limiter.request_queue = deque()
    limiter.priority_queue = deque()
    limiter.global_requests = deque()


def _request(func, **kwargs):
    return {
        "func": func,
        "args": (),
        "kwargs": kwargs,
        "user_id": 42,
        "file_identifier": None,
    }


def _contended(retry_after=20.0):
    from Thunder.utils.canonical_files import IngestContended

    return IngestContended("uniq-file-1", retry_after)


@pytest.mark.unit
async def test_contention_requeues_instead_of_running(limiter):
    """The worker must be handed back, so the request is put back on the queue
    with a not_before rather than being executed here."""
    requeues = []

    async def fake_requeue(request_data, queue_type, delay=0.0):
        requeues.append((queue_type, delay))

    limiter._requeue_request = fake_requeue
    ran = []

    async def handler():
        ran.append(1)
        raise _contended()

    request = _request(handler)
    assert await _drive(limiter, request) is True

    assert len(requeues) == 1, "contention did not requeue"
    assert len(ran) == 1, "the handler ran more than once for one _process_one call"
    assert requeues[0][1] == 20.0, "the retry_after from the exception was not used"


@pytest.mark.unit
async def test_a_zero_or_negative_delay_is_floored(limiter):
    """A sub-second requeue would re-contend immediately, in a tight loop."""
    requeues = []

    async def fake_requeue(request_data, queue_type, delay=0.0):
        requeues.append(delay)

    limiter._requeue_request = fake_requeue

    async def handler():
        raise _contended(retry_after=0.0)

    await _drive(limiter, _request(handler))
    assert requeues == [1.0]


@pytest.mark.unit
async def test_contention_is_capped_so_a_crashed_worker_cannot_loop_forever(limiter):
    """A crashed worker's claim outlives the TTL with no record ever appearing.
    Without a cap the user waits forever on a file that will never exist."""
    requeues = []

    async def fake_requeue(request_data, queue_type, delay=0.0):
        requeues.append(delay)

    limiter._requeue_request = fake_requeue

    async def handler():
        raise _contended(retry_after=20.0)

    request = _request(handler)
    for _ in range(rl.MAX_CONTENDED_ATTEMPTS + 2):
        await _drive(limiter, request)

    assert len(requeues) == rl.MAX_CONTENDED_ATTEMPTS, (
        f"{len(requeues)} requeues, expected the cap of {rl.MAX_CONTENDED_ATTEMPTS}"
    )
    assert limiter.notified, "the user was never told the file will not appear"


@pytest.mark.unit
async def test_contention_and_floodwait_use_separate_counters(limiter):
    """One must not mask the other: a request that FloodWaited several times must
    still get its full contention allowance, and vice versa."""
    from pyrogram.errors import FloodWait

    limiter._requeue_request = _noop_requeue

    async def flood():
        raise FloodWait(3)

    async def contend():
        raise _contended()

    flood_request = _request(flood)
    await _drive(limiter, flood_request)
    await _drive(limiter, flood_request)
    assert flood_request["attempts"] == 2, "the FloodWait counter is not moving"
    assert "contended_attempts" not in flood_request, (
        "a FloodWait incremented the contention counter"
    )

    contend_request = _request(contend)
    await _drive(limiter, contend_request)
    assert contend_request["contended_attempts"] == 1
    assert "attempts" not in contend_request, (
        "contention incremented the FloodWait counter -- one can now mask the other"
    )

    # the FloodWait drop still fires on its own counter, unaffected by the
    # contention counter above (production cannot re-drive a dropped request;
    # this loop can, so only "did it fire" is meaningful here)
    for _ in range(rl.MAX_REQUEST_ATTEMPTS + 1):
        await _drive(limiter, flood_request)
    assert limiter.notified, "the FloodWait drop notification never fired"


@pytest.mark.unit
async def test_contention_is_not_logged_as_an_error(limiter, caplog):
    import logging

    limiter._requeue_request = _noop_requeue

    async def handler():
        raise _contended()

    await _drive(limiter, _request(handler))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "routine contention was logged at WARNING or above -- a busy file would flood the log"
    )


async def _noop_requeue(request_data, queue_type, delay=0.0):
    return None
