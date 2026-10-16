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
    """Worst case is the shared budget, plus one floor per step that draws on it,
    plus the clients step's own slice. Both counts are read out of the plan, so
    adding or re-bucketing a step cannot silently invalidate the bound.

    The bound that matters is the SMALLEST grace period we ship for, not
    compose's comfortable 60s: Heroku, Render and Kubernetes all SIGKILL at 30s
    and neither app.json nor render.yaml can raise it. A 40s budget x 2s floors
    gave a 50s worst case, so a stop could be killed before the touch flush ran
    -- the exact outcome this budget exists to prevent.
    """
    plan = main_mod._teardown_plan()
    shared = [s for s in plan if s.shared]
    exclusive = [s for s in plan if not s.shared]

    assert plan, "the teardown plan is empty?"
    assert shared, "no step draws on the shared budget?"
    assert exclusive, "no step has its own slice -- is the clients step still bucketed?"

    worst_case = (
        main_mod._SHUTDOWN_BUDGET_SECONDS
        + len(shared) * main_mod._TEARDOWN_FLOOR_SECONDS
        # the CEILING, not the cap for whatever client count happens to be
        # configured: the bound has to hold for the worst one
        + sum(main_mod._CLIENT_STOP_MAX_SECONDS for _ in exclusive)
    )

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
    # the shipped constants must agree with the plan, not restate it
    assert len(shared) == main_mod._TEARDOWN_STEP_COUNT, (
        f"_TEARDOWN_STEP_COUNT says {main_mod._TEARDOWN_STEP_COUNT} but the plan "
        f"has {len(shared)} steps drawing on the shared budget"
    )
    assert pytest.approx(worst_case) == main_mod._SHUTDOWN_WORST_CASE_SECONDS, (
        f"the module's own worst-case figure is "
        f"{main_mod._SHUTDOWN_WORST_CASE_SECONDS}s but the plan gives {worst_case}s"
    )


@pytest.mark.unit
async def test_the_clients_step_gets_its_own_slice_and_the_slice_is_what_binds(monkeypatch):
    """The scaled cap must actually be the GRANT, not just a stored number.

    This is the defect a tuple-shape assertion could never see. The grant is
    min(ceiling, budget_left), so while the clients step drew on the shared
    budget its 10s-per-client ceiling was capped by what the four cheap steps
    left over -- the scaling was inert, and 2+ clients were cut anyway. Proving
    it requires running _safe_teardown_step and reading the timeout it used.
    """
    import asyncio

    monkeypatch.setattr(main_mod, "multi_clients", {0: object(), 1: object(), 2: object()})

    seen: list[float] = []

    async def spy(awaitable, timeout=None):
        seen.append(timeout)
        await awaitable
        return None

    async def noop():
        return None

    cap = main_mod._client_stop_cap()
    monkeypatch.setattr(main_mod.asyncio, "wait_for", spy)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + main_mod._SHUTDOWN_BUDGET_SECONDS

    await main_mod._safe_teardown_step(noop, "clients", [], deadline, cap=cap, shared=False)

    assert seen == [cap], f"the clients step was granted {seen}, not its own cap of {cap}"
    assert cap > main_mod._SHUTDOWN_BUDGET_SECONDS, (
        f"the cap ({cap}s) does not exceed the shared budget "
        f"({main_mod._SHUTDOWN_BUDGET_SECONDS}s), so `shared=False` is not what "
        "makes it bind -- the exemption is doing nothing"
    )


@pytest.mark.unit
async def test_a_shared_step_is_still_bounded_by_the_shared_budget(monkeypatch):
    """The exemption must be narrow: every other step still shares."""
    import asyncio

    seen: list[float] = []

    async def spy(awaitable, timeout=None):
        seen.append(timeout)
        await awaitable
        return None

    async def noop():
        return None

    monkeypatch.setattr(main_mod.asyncio, "wait_for", spy)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + main_mod._SHUTDOWN_BUDGET_SECONDS

    await main_mod._safe_teardown_step(noop, "database", [], deadline, shared=True)

    assert seen == [main_mod._TEARDOWN_STEP_SECONDS], (
        f"a shared step was granted {seen} -- it must be the per-step ceiling, not something larger"
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
    names = [s.name for s in plan]

    assert names == ["rate limiter", "touch buffer", "shortener", "clients", "database"], (
        f"teardown order changed: {names}"
    )

    clients = next(s for s in plan if s.name == "clients")
    assert clients.cap == main_mod._client_stop_cap(), (
        f"the clients step is capped at {clients.cap}s for 3 clients; the generic "
        "ceiling would cut a healthy stop"
    )
    assert not clients.shared, (
        "the clients step draws on the shared budget, so the scaled cap is "
        "capped by whatever the other four steps left over"
    )
    for step in plan:
        if step.name == "clients":
            continue
        assert step.cap is None, f"{step.name} has an unexpected cap {step.cap}"
        assert step.shared, f"{step.name} should draw on the shared budget"


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

    # the plan's entry builds a _Step(...) call, which the keyword walk above
    # cannot see at all -- check it too rather than trusting one shape
    plan = main_mod._teardown_plan()
    step_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_Step"
    ]
    plan_entries = [
        n
        for n in step_calls
        if len(n.args) > 1 and getattr(n.args[1], "id", None) == "cleanup_clients"
    ]
    assert plan_entries, (
        "the teardown plan no longer builds a _Step for cleanup_clients -- the "
        "scan below is stale and would pass vacuously"
    )
    for entry in plan_entries:
        assert len(entry.args) > 2 and _is_scaled_cap(entry.args[2]), (
            f"line {entry.lineno}: the plan's clients entry has an unscaled cap"
        )
        # the cap is positional (name, run, cap); the exemption is the keyword
        # shared=False. Without it the grant is min(cap, budget_left) and the
        # whole scaling is inert.
        shared_kw = {kw.arg: kw.value for kw in entry.keywords}
        assert "shared" in shared_kw, (
            f"line {entry.lineno}: the plan's clients entry does not say "
            "shared=False, so the scaled cap is capped by the shared budget"
        )
        assert (
            isinstance(shared_kw["shared"], ast.Constant) and shared_kw["shared"].value is False
        ), f"line {entry.lineno}: shared= is not the literal False"
    # and the two views must agree, so neither can drift unnoticed
    assert len(plan_entries) == 1, f"{len(plan_entries)} clients entries in one plan"
    assert any(s.name == "clients" for s in plan)

    assert keyword_sites == 3, (
        f"expected 3 direct _safe_teardown_step(cleanup_clients, ...) calls "
        f"(three boot-failure paths), found {keyword_sites} -- the scan is stale"
    )


@pytest.mark.unit
async def test_the_touch_flush_precedes_the_database_close():
    """After db.close() the client rejects writes, so a flush that runs later
    loses every delta with no error."""
    names = [s.name for s in main_mod._teardown_plan()]
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
async def test_the_clients_slice_alone_leaves_room_inside_the_grace_period():
    """The slice is what can cut a multi-client stop, so it has to be small
    enough that the REST of the teardown still fits behind it.

    10s per client with no ceiling would be 100s for ten clients -- over the
    30s the PaaS manifests allow, and the whole point of the ceiling.
    """
    REAL_PAAS_GRACE_SECONDS = 30.0
    rest = (
        main_mod._SHUTDOWN_BUDGET_SECONDS
        + main_mod._TEARDOWN_STEP_COUNT * main_mod._TEARDOWN_FLOOR_SECONDS
    )
    assert main_mod._CLIENT_STOP_MAX_SECONDS + rest < REAL_PAAS_GRACE_SECONDS, (
        f"clients can take {main_mod._CLIENT_STOP_MAX_SECONDS}s and the rest "
        f"{rest:.1f}s, which does not fit {REAL_PAAS_GRACE_SECONDS:.0f}s"
    )


@pytest.mark.unit
async def test_an_empty_client_set_does_not_produce_a_zero_cap(monkeypatch):
    monkeypatch.setattr(main_mod, "multi_clients", {})
    assert main_mod._client_stop_cap() > 0


@pytest.mark.unit
async def test_budget_left_never_goes_negative():
    loop = asyncio.get_running_loop()
    assert main_mod._budget_left(loop.time() - 5.0) == 0.0
    assert main_mod._budget_left(loop.time() + 5.0) > 0.0
