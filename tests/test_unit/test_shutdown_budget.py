"""Shutdown budget helpers -- the container grace period depends on them.

SIGTERM arrives, the process has a bounded window to flush the touch buffer
and close Mongo before the runtime SIGKILLs it. These helpers decide how much
of that window each step gets, so they are pinned rather than left implicit.
"""

import asyncio

import pytest

from Thunder import __main__ as main_mod


def _deadline_in(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


@pytest.mark.unit
async def test_step_budget_is_capped_while_budget_remains():
    assert main_mod._step_budget(_deadline_in(30.0)) == main_mod._TEARDOWN_STEP_SECONDS


@pytest.mark.unit
async def test_step_budget_never_drops_to_zero():
    """A spent budget must still let db.close() run. Timing out at 0 s would
    record an error for a step that never ran and exit non-zero on a healthy
    stop -- which on Heroku/Render means a restart loop."""
    assert main_mod._step_budget(_deadline_in(-100.0)) == main_mod._TEARDOWN_FLOOR_SECONDS
    assert main_mod._TEARDOWN_FLOOR_SECONDS > 0


@pytest.mark.unit
async def test_step_budget_uses_whatever_is_left():
    """Midway through the budget, a step gets the remainder (above the floor)."""
    loop = asyncio.get_running_loop()
    assert main_mod._step_budget(loop.time() + 4.0) == pytest.approx(4.0, abs=0.5)


@pytest.mark.unit
async def test_total_teardown_fits_the_compose_grace_period():
    """Worst case is the shared budget plus one floor per teardown step; that
    bound has to stay under docker-compose's stop_grace_period (60 s)."""
    steps = 5  # rate limiter, touch buffer, shortener, clients, database
    worst_case = main_mod._SHUTDOWN_BUDGET_SECONDS + steps * main_mod._TEARDOWN_FLOOR_SECONDS
    assert worst_case < 60.0, f"shutdown can outlive the container grace period ({worst_case}s)"


@pytest.mark.unit
async def test_budget_left_never_goes_negative():
    loop = asyncio.get_running_loop()
    assert main_mod._budget_left(loop.time() - 5.0) == 0.0
    assert main_mod._budget_left(loop.time() + 5.0) > 0.0
