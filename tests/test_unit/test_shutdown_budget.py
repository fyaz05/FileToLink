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
async def test_every_cleanup_clients_call_site_is_scaled():
    """shutdown_services is not the only caller. The three boot-failure paths
    also stop clients, and they used the generic ceiling -- so a multi-client
    boot failure left sessions half-stopped, leaking Telegram session files.

    Structural, because the alternative is a test that waits out real timeouts
    on three code paths nobody can exercise cheaply.
    """
    import ast
    import inspect

    def _is_scaled_cap(node) -> bool:
        # _client_stop_cap() -- a Call node, not a Name
        return getattr(getattr(node, "func", None), "id", None) == "_client_stop_cap"

    tree = ast.parse(inspect.getsource(main_mod))

    keyword_sites = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_safe_teardown_step"
            and node.args
            and getattr(node.args[0], "id", None) == "cleanup_clients"
        ):
            continue
        keyword_sites += 1
        caps = {kw.arg: kw.value for kw in node.keywords}
        assert "cap" in caps, (
            f"line {node.lineno}: cleanup_clients is stopped with the generic "
            "ceiling, which cuts a multi-client stop"
        )
        assert _is_scaled_cap(caps["cap"]), f"line {node.lineno}: the cap is not the scaled one"

    # the plan's entry passes the cap POSITIONALLY, so the keyword walk above
    # cannot see it -- check it too rather than trusting one shape
    plan = main_mod._teardown_plan()
    entries = [
        node for node in ast.walk(tree) if isinstance(node, ast.Tuple) and len(node.elts) == 3
    ]
    plan_entries = [n for n in entries if getattr(n.elts[1], "id", None) == "cleanup_clients"]
    assert plan_entries, "the teardown plan no longer lists cleanup_clients as a tuple"
    for entry in plan_entries:
        assert _is_scaled_cap(entry.elts[2]), (
            f"line {entry.lineno}: the plan's clients entry has an unscaled cap"
        )
    # and the two views must agree, so neither can drift unnoticed
    assert len(plan_entries) == 1, f"{len(plan_entries)} clients entries in one plan"
    assert any(name == "clients" for name, _s, _c in plan)

    assert keyword_sites == 3, (
        f"expected 3 direct _safe_teardown_step(cleanup_clients, ...) calls "
        f"(three boot-failure paths), found {keyword_sites} -- the scan is stale"
    )


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
