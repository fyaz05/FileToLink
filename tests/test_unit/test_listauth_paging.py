"""/listauth paging -- Telegram counts UTF-16 code units, not code points.

An emoji costs two units, so measuring with len() let an emoji-dense page
over the 4096 cap and the send failed. The page loop must also survive one
failed page instead of silently dropping the rest of the list.
"""

import pytest

from Thunder.bot.plugins import admin


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@pytest.mark.unit
def test_telegram_len_counts_emoji_as_two_units():
    assert admin._telegram_len("abc") == 3
    assert admin._telegram_len("😀") == 2
    assert admin._telegram_len("a😀") == 3
    assert admin._telegram_len("") == 0


@pytest.mark.unit
def test_emoji_page_measured_by_len_would_overflow():
    """The bug this guards: 1800 emoji is 1800 code points but 3600 UTF-16
    units, so a len()-based budget would ship a page over Telegram's cap."""
    page = "😀" * 1800
    assert len(page) == 1800
    assert admin._telegram_len(page) == 3600


@pytest.mark.unit
def test_budget_leaves_headroom_under_the_4096_cap():
    assert admin._PAGE_BUDGET_UNITS < 4096
    assert admin._telegram_len("x" * admin._PAGE_BUDGET_UNITS) == admin._PAGE_BUDGET_UNITS


@pytest.mark.unit
def test_page_delay_is_set_for_multi_page_lists():
    assert admin._PAGE_SEND_DELAY_SECONDS > 0
