"""Helper semantics: retry-then-retry, exhaustion, timeout."""

import asyncio

import pytest
from pyrogram.errors import FloodWait

from Thunder.utils.safe_call import tg_call


class Flaky:
    """Calls fail N times with FloodWait, then succeed."""

    def __init__(self, failures: int, wait: float = 0.01):
        self.failures = failures
        self.wait = wait
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise FloodWait(value=self.wait)
        return "ok"


@pytest.mark.unit
async def test_retries_through_floodwait():
    flaky = Flaky(failures=1)
    assert await tg_call(flaky) == "ok"
    assert flaky.calls == 2


@pytest.mark.unit
async def test_exhaustion_raises():
    flaky = Flaky(failures=3)
    with pytest.raises(FloodWait):
        await tg_call(flaky, retries=1)
    assert flaky.calls == 2  # initial + one retry


@pytest.mark.unit
async def test_zero_retries_propagates_immediately():
    flaky = Flaky(failures=1)
    with pytest.raises(FloodWait):
        await tg_call(flaky, retries=0, timeout=0)
    assert flaky.calls == 1


@pytest.mark.unit
async def test_timeout_fires():
    async def hang():
        await asyncio.sleep(5)

    with pytest.raises(asyncio.TimeoutError):
        await tg_call(hang, timeout=0.05, retries=0)


@pytest.mark.unit
async def test_lightweight_shape_caps_floodwait_sleep(monkeypatch):
    """Non-media RPCs cap the FloodWait sleep at 30s (wall-clock budget)."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("Thunder.utils.safe_call.asyncio.sleep", fake_sleep)

    async def get_me():
        raise FloodWait(value=90)

    with pytest.raises(FloodWait):
        await tg_call(get_me, retries=1, timeout=0)
    assert slept == [30.0]


@pytest.mark.unit
async def test_media_shape_sleeps_full_floodwait(monkeypatch):
    """File-transfer shapes (e.g. copy) ride out long FloodWaits up to the
    600s media ceiling -- sustained throttle must not fail the ingest path."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("Thunder.utils.safe_call.asyncio.sleep", fake_sleep)

    async def copy(*args, **kwargs):
        raise FloodWait(value=90)

    with pytest.raises(FloodWait):
        await tg_call(copy, retries=1)
    assert slept == [90.0]


@pytest.mark.unit
async def test_media_shape_floodwait_ceiling(monkeypatch):
    """Waits beyond the 600s media ceiling are clamped, never unbounded."""
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("Thunder.utils.safe_call.asyncio.sleep", fake_sleep)

    async def copy(*args, **kwargs):
        raise FloodWait(value=3600)

    with pytest.raises(FloodWait):
        await tg_call(copy, retries=1)
    assert slept == [600.0]
