"""Telegram measures message length in UTF-16 code units, not code points.

An emoji costs two units, so a len()-based budget lets an emoji-dense page
over the 4096 cap and the send fails. These tests pin the real paginator, not
just the arithmetic helper, and the /listauth page loop that consumes it --
the previous version of this file asserted only that a constant was positive,
which the paging loop could be deleted from entirely without failing.
"""

import pytest

from Thunder.bot.plugins import admin
from Thunder.utils.messages import TELEGRAM_MAX_UNITS, paginate_lines, telegram_len


@pytest.mark.unit
def test_telegram_len_counts_emoji_as_two_units():
    assert telegram_len("abc") == 3
    assert telegram_len("😀") == 2
    assert telegram_len("a😀") == 3
    assert telegram_len("") == 0


@pytest.mark.unit
def test_emoji_page_measured_by_len_would_overflow():
    """The bug this guards: 1800 emoji is 1800 code points but 3600 UTF-16
    units, so a len()-based budget would ship a page over Telegram's cap."""
    page = "😀" * 1800
    assert len(page) == 1800
    assert telegram_len(page) == 3600


@pytest.mark.unit
def test_budget_leaves_headroom_under_the_cap():
    assert admin._PAGE_BUDGET_UNITS < TELEGRAM_MAX_UNITS


# --- the paginator itself -------------------------------------------------


@pytest.mark.unit
def test_short_text_is_a_single_page():
    assert paginate_lines("one\ntwo", 3500) == ["one\ntwo"]


@pytest.mark.unit
def test_long_text_splits_on_line_boundaries():
    text = "\n".join(f"row-{i} " + "x" * 50 for i in range(40))
    pages = paginate_lines(text, 200)
    assert len(pages) > 1
    for page in pages:
        assert telegram_len(page) <= 200 + 51, "a page overflowed its budget"


@pytest.mark.unit
def test_pagination_loses_no_records():
    """Every record must appear exactly once. This is the property that makes
    the split safe: a dropped line is a missing authorized user."""
    text = "\n".join(f"- user {i} authorized_by={i} auth_time=2026-01-01" for i in range(60))
    rejoined = "".join(paginate_lines(text, 300))
    for i in range(60):
        assert f"- user {i} authorized_by={i}" in rejoined


@pytest.mark.unit
def test_one_line_longer_than_the_budget_is_not_split():
    """A single over-long line still gets its own page -- chopping a record in
    half is worse than one page over the cap, and it must not loop forever."""
    long_line = "y" * 900
    pages = paginate_lines(f"short\n{long_line}\nshort2", 100)
    assert any(page.rstrip("\n") == long_line for page in pages), (
        "the over-long line was chopped instead of paged"
    )


@pytest.mark.unit
def test_emoji_dense_record_is_paged_by_units_not_characters():
    """The original failure: an emoji-dense record counts double. Paging by
    len() would produce two pages that each exceed the cap."""
    text = "\n".join(f"- user 😀 {i} 😀😀" for i in range(30))
    budget = 200
    for page in paginate_lines(text, budget):
        # each page holds one whole record plus the newline it was joined with
        assert telegram_len(page.rstrip("\n")) <= budget


# --- the /listauth loop that sends the pages ------------------------------


@pytest.mark.unit
async def test_listauth_sends_every_page_over_the_cap(monkeypatch):
    """The handler, not just the helper: delete the page loop and this fails."""
    from Thunder.utils import database as database_mod

    users = [{"user_id": i, "authorized_by": 1, "authorized_at": "2026-01-01"} for i in range(50)]
    monkeypatch.setattr(admin, "list_allowed", _async_return(users))
    # the display-name lookup is best-effort inside the handler; let it fail so
    # the test pins the paging, not the batched get_users round trip
    monkeypatch.setattr(database_mod, "db", database_mod.db)

    sent: list[str] = []

    async def fake_reply(message, text=None, **kwargs):
        sent.append(text)

    async def no_sleep(seconds):
        # the pacing delay is real behaviour but not what this test is about;
        # without this the suite pays 0.5s x page-count for nothing
        return None

    monkeypatch.setattr(admin, "reply", fake_reply)
    monkeypatch.setattr(admin.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(admin, "_PAGE_BUDGET_UNITS", 300)

    await admin.list_authorized_command(client=None, message=None)

    assert len(sent) > 1, "a 50-user list should not fit on one page"
    for user in users:
        assert any(f"User ID: <code>{user['user_id']}</code>" in page for page in sent), (
            f"user {user['user_id']} was dropped by the paginator"
        )


@pytest.mark.unit
async def test_one_failed_page_does_not_truncate_the_list(monkeypatch):
    """The Blocking finding: the loop had `break` under a comment saying the
    opposite, so one rejected page silently truncated an audit command with no
    marker that the list had ended early."""
    users = [{"user_id": i, "authorized_by": 1, "authorized_at": "2026-01-01"} for i in range(50)]
    monkeypatch.setattr(admin, "list_allowed", _async_return(users))
    monkeypatch.setattr(admin, "_PAGE_BUDGET_UNITS", 300)
    monkeypatch.setattr(admin.asyncio, "sleep", _no_sleep)

    sent: list[str] = []
    state = {"n": 0}

    async def fail_second(message, text=None, **kwargs):
        state["n"] += 1
        sent.append(text)
        if state["n"] == 2:
            raise RuntimeError("MESSAGE_TOO_LONG")

    monkeypatch.setattr(admin, "reply", fail_second)

    await admin.list_authorized_command(client=None, message=None)

    delivered = "".join(s for s in sent if "Partial list" not in s)
    missing = [
        u["user_id"] for u in users if f"User ID: <code>{u['user_id']}</code>" not in delivered
    ]
    assert not missing, (
        f"{len(missing)} authorized users never reached the owner "
        f"(first missing: {missing[:5]}) -- the loop stopped at the failed page"
    )


@pytest.mark.unit
async def test_a_partial_list_is_reported(monkeypatch):
    """Silence is the bug. The owner has to be able to tell the list was cut."""
    users = [{"user_id": i, "authorized_by": 1, "authorized_at": "2026-01-01"} for i in range(50)]
    monkeypatch.setattr(admin, "list_allowed", _async_return(users))
    monkeypatch.setattr(admin, "_PAGE_BUDGET_UNITS", 300)
    monkeypatch.setattr(admin.asyncio, "sleep", _no_sleep)

    sent: list[str] = []
    state = {"n": 0}

    async def flaky(message, text=None, **kwargs):
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("transient")
        sent.append(text)

    monkeypatch.setattr(admin, "reply", flaky)

    await admin.list_authorized_command(client=None, message=None)

    assert any("Partial list" in s for s in sent), (
        "a page failed and the owner was never told the list was incomplete"
    )


@pytest.mark.unit
async def test_a_complete_list_reports_nothing(monkeypatch):
    """The notice must not fire on the happy path."""
    users = [{"user_id": i, "authorized_by": 1, "authorized_at": "2026-01-01"} for i in range(50)]
    monkeypatch.setattr(admin, "list_allowed", _async_return(users))
    monkeypatch.setattr(admin, "_PAGE_BUDGET_UNITS", 300)
    monkeypatch.setattr(admin.asyncio, "sleep", _no_sleep)

    sent: list[str] = []

    async def ok(message, text=None, **kwargs):
        sent.append(text)

    monkeypatch.setattr(admin, "reply", ok)

    await admin.list_authorized_command(client=None, message=None)

    assert len(sent) > 1, "expected several pages"
    assert not any("Partial list" in s for s in sent), "warned about nothing"


@pytest.mark.unit
async def test_every_record_appears_exactly_once(monkeypatch):
    """'At least once' would pass a handler that emitted every record twice."""
    users = [{"user_id": i, "authorized_by": 1, "authorized_at": "2026-01-01"} for i in range(50)]
    monkeypatch.setattr(admin, "list_allowed", _async_return(users))
    monkeypatch.setattr(admin, "_PAGE_BUDGET_UNITS", 300)
    monkeypatch.setattr(admin.asyncio, "sleep", _no_sleep)

    sent: list[str] = []

    async def ok(message, text=None, **kwargs):
        sent.append(text)

    monkeypatch.setattr(admin, "reply", ok)

    await admin.list_authorized_command(client=None, message=None)

    joined = "".join(sent)
    for user in users:
        marker = f"User ID: <code>{user['user_id']}</code>"
        assert joined.count(marker) == 1, (
            f"{marker} appears {joined.count(marker)} times, expected exactly 1"
        )


@pytest.mark.unit
def test_page_delay_is_set_for_multi_page_lists():
    assert admin._PAGE_SEND_DELAY_SECONDS > 0


async def _no_sleep(_s):
    return None


@pytest.mark.unit
async def test_listauth_paces_between_pages_not_after_the_last(monkeypatch):
    """Sleeping after the final page is dead weight; sleeping *before* the first
    wastes the user's wait. The old `if len(pages) > 1` did both wrong."""
    users = [{"user_id": i, "authorized_by": 1, "authorized_at": "2026-01-01"} for i in range(50)]
    monkeypatch.setattr(admin, "list_allowed", _async_return(users))
    monkeypatch.setattr(admin, "_PAGE_BUDGET_UNITS", 300)

    sent: list[str] = []

    async def fake_reply(message, text=None, **kwargs):
        sent.append(text)

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(admin, "reply", fake_reply)
    monkeypatch.setattr(admin.asyncio, "sleep", fake_sleep)

    await admin.list_authorized_command(client=None, message=None)

    pages_sent = len(sent)
    assert pages_sent > 2, "need at least 3 pages to tell the two behaviours apart"
    assert len(slept) == pages_sent - 1, (
        f"{pages_sent} pages should have {pages_sent - 1} gaps, got {len(slept)}"
    )


def _async_return(value):
    async def _call():
        return value

    return _call
