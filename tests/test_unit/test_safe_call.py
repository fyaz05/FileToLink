"""H4a helper semantics: retry-then-retry, exhaustion, timeout."""

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
async def test_on_error_hook_sees_exception():
    seen = []

    async def boom():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await tg_call(boom, on_error=seen.append, timeout=0, retries=0)
    assert isinstance(seen[0], ValueError)
