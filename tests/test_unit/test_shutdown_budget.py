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
async def test_total_teardown_fits_the_PaaS_grace_period():
    """Worst case is the shared budget plus one floor per teardown step. The
    step count is read out of shutdown_services itself, so adding a sixth step
    cannot silently invalidate the bound this test claims to protect.

    The bound that matters is the SMALLEST grace period we ship for, not
    compose's comfortable 60s: Heroku, Render and Kubernetes all SIGKILL at 30s
    and neither app.json nor render.yaml can raise it. A 40s budget x 2s floors
    gave a 50s worst case, so a stop could be killed before the touch flush ran
    -- the exact outcome this budget exists to prevent.
    """
    steps = len(main_mod._teardown_plan())

    assert steps >= 1, "the teardown plan is empty?"
    worst_case = main_mod._SHUTDOWN_BUDGET_SECONDS + steps * main_mod._TEARDOWN_FLOOR_SECONDS

    # Spelled out here rather than read from the module. The PaaS limit is a
    # fact about Heroku/Render/Kubernetes, not a constant we get to define --
    # and a test that read _MIN_PAAS_GRACE_SECONDS would merely agree with the
    # module's own import-time assert, so raising both would keep it green.
    REAL_PAAS_GRACE_SECONDS = 30.0
    assert worst_case < REAL_PAAS_GRACE_SECONDS, (
        f"shutdown can take {worst_case:.1f}s but Heroku, Render and Kubernetes "
        f"only allow {REAL_PAAS_GRACE_SECONDS:.0f}s -- a stop gets SIGKILLed "
        "before the touch flush and db.close() run"
    )
    assert main_mod._MIN_PAAS_GRACE_SECONDS <= REAL_PAAS_GRACE_SECONDS, (
        f"_MIN_PAAS_GRACE_SECONDS claims {main_mod._MIN_PAAS_GRACE_SECONDS}s, "
        f"more than the {REAL_PAAS_GRACE_SECONDS:.0f}s the PaaS manifests allow"
    )
    # the shipped constant must agree with the plan
    assert steps == main_mod._TEARDOWN_STEP_COUNT, (
        f"_TEARDOWN_STEP_COUNT says {main_mod._TEARDOWN_STEP_COUNT} but the plan has {steps} steps"
    )


@pytest.mark.unit
async def test_the_teardown_plan_is_ordered_and_the_clients_step_is_scaled(monkeypatch):
    """The wiring itself, not the clock.

    A test that merely watches shutdown_services finish cannot tell a correct
    per-step cap from one that was never passed: with the cap pinned back to
    the generic 8s the multi-client stop still completed, because a test's fake
    cleanup returns long before any cap is reached.
    """
    monkeypatch.setattr(main_mod, "multi_clients", {0: object(), 1: object(), 2: object()})

    plan = main_mod._teardown_plan()
    names = [name for name, _step, _cap in plan]

    assert names == ["rate limiter", "touch buffer", "shortener", "clients", "database"], (
        f"teardown order changed: {names}"
    )

    caps = {name: cap for name, _step, cap in plan}
    assert caps["clients"] == 3 * main_mod._CLIENT_STOP_SECONDS, (
        f"the clients step is capped at {caps['clients']}s for 3 clients; the "
        "generic ceiling would cut a healthy stop"
    )
    for name in ("rate limiter", "touch buffer", "shortener", "database"):
        assert caps[name] is None, f"{name} has an unexpected cap {caps[name]}"


@pytest.mark.unit
async def test_the_touch_flush_precedes_the_database_close():
    """After db.close() the client rejects writes, so a flush that runs later
    loses every delta with no error."""
    names = [name for name, _step, _cap in main_mod._teardown_plan()]
    assert names.index("touch buffer") < names.index("database"), (
        "the touch buffer is flushed after the database is closed -- those deltas are lost"
    )


@pytest.mark.unit
async def test_the_client_stop_cap_scales_with_the_client_count(monkeypatch):
    """cleanup_clients stops every client individually at 10s each. Under the
    generic 8s step cap a healthy two-client stop was cut, recorded as an error,
    and exited 1 -- restarting a PaaS that was shutting down fine."""
    monkeypatch.setattr(main_mod, "multi_clients", {0: object()})
    assert main_mod._client_stop_cap() == main_mod._CLIENT_STOP_SECONDS

    monkeypatch.setattr(main_mod, "multi_clients", {0: object(), 1: object()})
    two = main_mod._client_stop_cap()
    assert two == 2 * main_mod._CLIENT_STOP_SECONDS
    assert two > main_mod._TEARDOWN_STEP_SECONDS, (
        "the cap for 2 clients is no larger than the generic step cap, so it "
        "would be cut exactly as before"
    )


@pytest.mark.unit
async def test_the_client_stop_cap_stays_bounded(monkeypatch):
    """40 clients must not hold the shutdown open for 400s."""
    monkeypatch.setattr(main_mod, "multi_clients", {i: object() for i in range(40)})
    assert main_mod._client_stop_cap() == main_mod._CLIENT_STOP_MAX_SECONDS


@pytest.mark.unit
async def test_an_empty_client_set_does_not_produce_a_zero_cap(monkeypatch):
    monkeypatch.setattr(main_mod, "multi_clients", {})
    assert main_mod._client_stop_cap() > 0


@pytest.mark.unit
async def test_budget_left_never_goes_negative():
    loop = asyncio.get_running_loop()
    assert main_mod._budget_left(loop.time() - 5.0) == 0.0
    assert main_mod._budget_left(loop.time() + 5.0) > 0.0
