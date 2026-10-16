"""Queue-full must replace the queue notice, not sit next to it.

The notice is sent *before* the request is admitted, so a full queue left the
user staring at "Status: In Queue" with no sign the request was rejected.
"""

import pytest

from Thunder.utils import rate_limiter as rl
from Thunder.utils.messages import MSG_RATE_LIMIT_QUEUE_FULL
from Thunder.vars import Var


class _User:
    id = 4242


class _Bot:
    """The notice is sent BY the bot, so Pyrogram stamps the bot as author."""

    id = 999


class _Notice:
    """Stand-in for the already-sent queue notification."""

    from_user = _Bot()
    id = 99
    text = "Status: In Queue"


@pytest.mark.unit
async def test_queue_full_edits_the_existing_notice(monkeypatch):
    edits = []
    asked: list[int] = []

    async def _edit(msg, text, **kwargs):
        edits.append((msg, text))

    async def _estimate(user_id, file_identifier=None):
        asked.append(user_id)
        return 0.0

    monkeypatch.setattr(rl, "edit_safe", _edit)
    monkeypatch.setattr(rl.rate_limiter, "estimate_wait_time", _estimate)

    notice = _Notice()
    await rl.replace_queue_notification(object(), notice, None, 4242)

    assert len(edits) == 1, "the stale notice was not updated"
    msg, text = edits[0]
    assert msg is notice
    assert "In Queue" not in text
    assert text == MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=1, s="")


@pytest.mark.unit
async def test_wait_estimate_uses_the_requester_not_the_bot(monkeypatch):
    """The notice is authored by the bot, so reading from_user off it estimated
    the wait against the bot's own empty window and always said "less than a
    minute" -- the requester never saw their real queue time."""
    asked: list[int] = []

    async def _edit(msg, text, **kwargs):
        return None

    async def _estimate(user_id, file_identifier=None):
        asked.append(user_id)
        return 0.0

    monkeypatch.setattr(rl, "edit_safe", _edit)
    monkeypatch.setattr(rl.rate_limiter, "estimate_wait_time", _estimate)

    notice = _Notice()
    assert notice.from_user.id != 4242, "fake no longer models a bot-authored notice"
    await rl.replace_queue_notification(object(), notice, None, 4242)

    assert asked == [4242], f"wait estimated for the wrong id: {asked}"


@pytest.mark.unit
async def test_queue_full_pluralises_and_rounds_up(monkeypatch):
    edits = []

    async def _edit(msg, text, **kwargs):
        edits.append(text)

    async def _estimate(user_id, file_identifier=None):
        return 130.0  # 2.16 minutes -> "3 minutes"

    monkeypatch.setattr(rl, "edit_safe", _edit)
    monkeypatch.setattr(rl.rate_limiter, "estimate_wait_time", _estimate)

    await rl.replace_queue_notification(object(), _Notice(), None, 4242)

    assert edits == [MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=3, s="s")]


@pytest.mark.unit
def test_global_rps_cap_is_live_without_the_per_minute_window(monkeypatch):
    """The old warning told operators GLOBAL_RPS_LIMIT had "no effect" until
    the global breaker was enabled. _breaker_rate returns the cap ahead of the
    per-minute window, so it is enforced on its own -- pin that, because the
    warning text is derived from this behaviour."""
    monkeypatch.setattr(Var, "GLOBAL_RPS_LIMIT", 5)
    monkeypatch.setattr(Var, "GLOBAL_RATE_LIMIT", False)

    limiter = rl.RateLimiter.__new__(rl.RateLimiter)
    limiter.global_rate_limit_enabled = False
    limiter.max_global_requests_per_minute = 600  # would be 10/s if it won

    assert limiter._breaker_rate() == 5.0
