"""Keepalive survives ping failures and remains cancellable."""
import asyncio

import pytest

from Thunder.utils import keepalive

_REAL_SLEEP = asyncio.sleep


class FakeResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.closed = 0

    def get(self, url):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)

    async def close(self):
        self.closed += 1


def run_pings(monkeypatch, outcomes, ticks, session_factory=None):
    session = FakeSession(outcomes)
    monkeypatch.setattr(
        keepalive.aiohttp, "ClientSession",
        session_factory or (lambda *a, **k: session))
    monkeypatch.setattr(keepalive.Var, "PING_INTERVAL", 0, raising=False)
    monkeypatch.setattr(keepalive.Var, "URL", "https://example.com/", raising=False)

    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > ticks:
            raise asyncio.CancelledError
        await _REAL_SLEEP(0)  # yield; never re-enter the patched version

    monkeypatch.setattr(keepalive.asyncio, "sleep", fake_sleep)

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await keepalive.ping_server()

    asyncio.run(main())
    return session


def test_a_network_error_does_not_end_the_loop(monkeypatch):
    session = run_pings(monkeypatch, [OSError("Name or service not known")], ticks=3)
    assert session.calls == 3, (
        f"keepalive stopped after {session.calls} attempt(s) instead of continuing")


def test_repeated_errors_are_all_survived(monkeypatch):
    session = run_pings(monkeypatch, [OSError("boom")] * 3, ticks=4)
    assert session.calls == 4


def test_a_non_200_status_does_not_end_the_loop(monkeypatch):
    session = run_pings(monkeypatch, [503, 500], ticks=3)
    assert session.calls == 3


def test_it_keeps_pinging_when_everything_is_healthy(monkeypatch):
    session = run_pings(monkeypatch, [], ticks=4)
    assert session.calls == 4


def test_failing_to_build_a_session_does_not_end_the_loop(monkeypatch):
    attempts = {"n": 0}
    good = FakeSession([])

    def flaky(*a, **k):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("cannot create session")
        return good

    run_pings(monkeypatch, [], ticks=3, session_factory=flaky)
    assert attempts["n"] >= 2, "the keepalive gave up instead of rebuilding the session"
    assert good.calls >= 1


def test_cancellation_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(keepalive.aiohttp, "ClientSession", lambda *a, **k: FakeSession([]))
    monkeypatch.setattr(keepalive.Var, "PING_INTERVAL", 0, raising=False)
    monkeypatch.setattr(keepalive.Var, "URL", "https://example.com/", raising=False)

    async def raise_cancel(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(keepalive.asyncio, "sleep", raise_cancel)

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await keepalive.ping_server()

    asyncio.run(main())
