"""Post-boot channel watcher: state transitions and error handling."""
import asyncio

import pytest

from Thunder.utils import channel_watch
from Thunder.utils.channel_watch import BROKEN, HEALTHY, ChannelWatch

_REAL_SLEEP = asyncio.sleep


class Probe:

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if len(self.outcomes) > 1:
            outcome = self.outcomes.pop(0)
        else:
            outcome = self.outcomes[0] if self.outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def test_a_healthy_channel_says_nothing():
    watcher = ChannelWatch(Probe([None]))
    assert await watcher.check_once() is None
    assert watcher.state == HEALTHY


async def test_losing_access_is_reported_once():
    watcher = ChannelWatch(Probe(["not a member"]))
    message = await watcher.check_once()
    assert message is not None
    assert "not a member" in message
    assert watcher.state == BROKEN


async def test_a_persistent_problem_is_reported_only_once():
    watcher = ChannelWatch(Probe(["still not a member"]))
    first = await watcher.check_once()
    watcher.message_delivered()
    second = await watcher.check_once()
    third = await watcher.check_once()
    assert first is not None
    assert second is None and third is None


async def test_recovery_is_reported():
    watcher = ChannelWatch(Probe(["not a member", None]))
    await watcher.check_once()
    watcher.message_delivered()
    message = await watcher.check_once()
    assert message is not None
    assert "restored" in message.lower()
    watcher.message_delivered()
    assert watcher.state == HEALTHY


async def test_an_undelivered_recovery_message_is_retried():
    watcher = ChannelWatch(Probe(["broken", None, None]))
    await watcher.check_once()
    watcher.message_delivered()
    recovery = await watcher.check_once()
    assert recovery is not None
    assert "restored" in recovery.lower()
    watcher.message_undelivered()
    retry = await watcher.check_once()
    assert retry is not None and retry == recovery
    watcher.message_delivered()
    assert await watcher.check_once() is None
    assert watcher.state == HEALTHY


async def test_a_second_recovery_does_not_alert_again():
    watcher = ChannelWatch(Probe(["broken", None, None, None]))
    await watcher.check_once()
    watcher.message_delivered()
    message = await watcher.check_once()
    assert message is not None
    assert "restored" in message.lower()
    watcher.message_delivered()
    assert await watcher.check_once() is None
    assert await watcher.check_once() is None


async def test_a_failing_probe_is_not_treated_as_lost_access():
    watcher = ChannelWatch(Probe([OSError("name resolution failed")]))
    assert await watcher.check_once() is None
    assert watcher.state == HEALTHY


async def test_a_failing_probe_does_not_mask_an_ongoing_outage():
    watcher = ChannelWatch(Probe(["broken", OSError("blip")]))
    await watcher.check_once()
    assert await watcher.check_once() is None
    assert watcher.state == BROKEN


async def test_a_blip_then_a_real_recovery_still_reports():
    watcher = ChannelWatch(Probe(["broken", OSError("blip"), None]))
    await watcher.check_once()
    watcher.message_delivered()
    assert await watcher.check_once() is None
    message = await watcher.check_once()
    assert message is not None and "restored" in message.lower()


def run_watcher(monkeypatch, outcomes, ticks):
    notifications = []
    probe = Probe(outcomes)

    async def notify(message):
        notifications.append(message)

    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > ticks:
            raise asyncio.CancelledError
        await _REAL_SLEEP(0)

    monkeypatch.setattr(channel_watch.asyncio, "sleep", fake_sleep)

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await channel_watch.watch_channel(notify, probe, 60)

    asyncio.run(main())
    return notifications, probe


def test_the_loop_keeps_running_across_notifications(monkeypatch):
    notifications, probe = run_watcher(
        monkeypatch, ["broken", None, "broken again"], ticks=4)
    assert len(notifications) == 3
    assert probe.calls == 4


def test_a_failing_probe_does_not_end_the_loop(monkeypatch):
    notifications, probe = run_watcher(
        monkeypatch, [OSError("boom"), None, "broken"], ticks=4)
    assert probe.calls == 4, "the watcher stopped after the error"
    assert len(notifications) == 1


def test_a_failing_notification_does_not_end_the_loop(monkeypatch):
    calls = {"notify": 0, "probe": 0}

    async def notify(_message):
        calls["notify"] += 1
        raise RuntimeError("telegram down")

    async def probe():
        calls["probe"] += 1
        return "broken"

    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 3:
            raise asyncio.CancelledError
        await _REAL_SLEEP(0)

    monkeypatch.setattr(channel_watch.asyncio, "sleep", fake_sleep)

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await channel_watch.watch_channel(notify, probe, 60)

    asyncio.run(main())
    assert calls["probe"] == 3, "a failed notification killed the watcher"


def test_the_interval_accepts_zero_to_mean_off():
    import pathlib
    import re

    source = (pathlib.Path(__file__).resolve().parent.parent
              / "Thunder" / "vars.py").read_text(encoding="utf-8")
    match = re.search(r'CHANNEL_WATCH_INTERVAL:\s*int\s*=\s*int_env\([^)]*\)', source)
    assert match, "CHANNEL_WATCH_INTERVAL is no longer declared"
    assert "minimum=0" in match.group(0), (
        "a positive minimum cannot switch the watcher off")


async def test_an_undelivered_transition_message_is_retried():
    watcher = ChannelWatch(Probe(["broken"]))
    first = await watcher.check_once()
    watcher.message_undelivered()
    retry = await watcher.check_once()
    assert retry == first
    watcher.message_delivered()
    assert await watcher.check_once() is None
    assert watcher.state == BROKEN


async def test_recovery_is_not_sent_while_the_break_message_is_undelivered():
    watcher = ChannelWatch(Probe(["broken", None]))
    await watcher.check_once()
    watcher.message_undelivered()
    assert await watcher.check_once() is None
    assert watcher.state == HEALTHY


async def test_a_pending_recovery_survives_a_renewed_outage():
    watcher = ChannelWatch(Probe(["broken", None, "broken", None, None]))
    outage = await watcher.check_once()
    watcher.message_delivered()
    recovery = await watcher.check_once()
    watcher.message_undelivered()

    assert outage is not None
    assert recovery is not None
    assert await watcher.check_once() is None
    retry = await watcher.check_once()
    assert retry == recovery
    watcher.message_delivered()
    assert await watcher.check_once() is None
    assert watcher.state == HEALTHY


def test_a_failed_notification_is_retried_until_it_lands(monkeypatch):
    calls = {"notify": 0}
    delivered = []

    async def notify(message):
        calls["notify"] += 1
        if calls["notify"] < 3:
            raise RuntimeError("telegram down")
        delivered.append(message)

    async def probe():
        return "not a member"

    sleeps = {"n": 0}

    async def fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 6:
            raise asyncio.CancelledError
        await _REAL_SLEEP(0)

    monkeypatch.setattr(channel_watch.asyncio, "sleep", fake_sleep)

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await channel_watch.watch_channel(notify, probe, 60)

    asyncio.run(main())
    assert len(delivered) == 1
    assert "no longer create links" in delivered[0]
    assert calls["notify"] == 3
