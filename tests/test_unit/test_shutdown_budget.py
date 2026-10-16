"""Shutdown budget helpers -- the container grace period depends on them.

SIGTERM arrives, the process has a bounded window to flush the touch buffer
and close Mongo before the runtime SIGKILLs it. These helpers decide how much
of that window each step gets, so they are pinned rather than left implicit.
"""

import asyncio
import inspect

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
        # app_runner.cleanup() is budgeted through _step_budget, which grants
        # the same floor, and it runs OUTSIDE _teardown_plan -- so counting
        # only the plan's steps under-counted the bound by a full floor.
        + main_mod._PRE_PLAN_FLOOR_STEPS * main_mod._TEARDOWN_FLOOR_SECONDS
        # the CEILING, not the cap for whatever client count is configured:
        # the bound has to hold for the worst one
        + sum(main_mod._CLIENT_STOP_SECONDS for _ in exclusive)
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
    """The slice must actually be the GRANT, not just a stored number.

    This is the defect a tuple-shape assertion could never see. The grant is
    min(ceiling, budget_left), so while the clients step drew on the shared
    budget its ceiling was capped by what the four cheap steps left over, and
    2+ clients were cut anyway. Proving it requires running _safe_teardown_step
    and reading the timeout it used.
    """
    seen: list[float] = []

    async def spy(awaitable, timeout=None):
        seen.append(timeout)
        await awaitable
        return None

    async def noop():
        return None

    cap = main_mod._CLIENT_STOP_SECONDS
    monkeypatch.setattr(main_mod.asyncio, "wait_for", spy)
    loop = asyncio.get_running_loop()
    # deliberately generous: the slice must be granted even with the whole
    # shared budget still unspent, which is the case that used to be impossible
    deadline = loop.time() + main_mod._SHUTDOWN_BUDGET_SECONDS + 60.0

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
    seen: list[float] = []

    async def spy(awaitable, timeout=None):
        seen.append(timeout)
        await awaitable
        return None

    async def noop():
        return None

    monkeypatch.setattr(main_mod.asyncio, "wait_for", spy)
    loop = asyncio.get_running_loop()
    # the budget and the step cap are both 8s, so a deadline at exactly the
    # budget makes this a coin flip on microseconds. Give it room so the step
    # cap is unambiguously the binding constraint.
    deadline = loop.time() + main_mod._SHUTDOWN_BUDGET_SECONDS + 60.0

    await main_mod._safe_teardown_step(noop, "database", [], deadline, shared=True)

    assert seen == [main_mod._TEARDOWN_STEP_SECONDS], (
        f"a shared step was granted {seen} -- it must be the per-step ceiling, not something larger"
    )


@pytest.mark.unit
async def test_a_shared_step_is_cut_once_the_budget_is_gone(monkeypatch):
    """The other half: a spent budget must not grant a shared step its ceiling."""
    seen: list[float] = []

    async def spy(awaitable, timeout=None):
        seen.append(timeout)
        await awaitable
        return None

    async def noop():
        return None

    monkeypatch.setattr(main_mod.asyncio, "wait_for", spy)
    loop = asyncio.get_running_loop()
    deadline = loop.time() - 5.0  # already spent

    await main_mod._safe_teardown_step(noop, "database", [], deadline, shared=True)

    assert seen == [main_mod._TEARDOWN_FLOOR_SECONDS], (
        f"a shared step past its deadline was granted {seen}; it should get the "
        "floor so db.close() still runs"
    )


@pytest.mark.unit
async def test_the_teardown_plan_is_ordered_and_the_clients_step_has_its_own_slice():
    """The wiring itself, not the clock.

    A test that merely watches shutdown_services finish cannot tell a correct
    per-step cap from one that was never passed: with the cap pinned back to
    the generic 8s the multi-client stop still completed, because a test's fake
    cleanup returns long before any cap is reached.
    """
    plan = main_mod._teardown_plan()
    names = [s.name for s in plan]

    assert names == ["rate limiter", "touch buffer", "shortener", "clients", "database"], (
        f"teardown order changed: {names}"
    )

    clients = next(s for s in plan if s.name == "clients")
    assert clients.cap == main_mod._CLIENT_STOP_SECONDS, (
        f"the clients step is capped at {clients.cap}s; the generic "
        f"{main_mod._TEARDOWN_STEP_SECONDS}s ceiling would cut a healthy stop"
    )
    assert clients.cap > main_mod._TEARDOWN_STEP_SECONDS, (
        f"the clients slice ({clients.cap}s) is not larger than the generic step "
        "ceiling, so it would be cut exactly as before"
    )
    assert not clients.shared, (
        "the clients step draws on the shared budget, so its cap is limited by "
        "whatever the other four steps left over"
    )
    for step in plan:
        if step.name == "clients":
            continue
        assert step.cap is None, f"{step.name} has an unexpected cap {step.cap}"
        assert step.shared, f"{step.name} should draw on the shared budget"


@pytest.mark.unit
async def test_the_clients_slice_is_flat_and_says_so():
    """Per-client scaling CANNOT fit the grace period, so it must not be faked.

    An earlier version computed min(12, n * 10). Because 12 < 2*10, the min()
    picked the constant for EVERY n >= 2 -- the scaling was decorative while
    the code and its tests both claimed it scaled, and the assertion that would
    have noticed (two == 2 * per-client) was deleted in the same commit.
    """
    src = inspect.getsource(main_mod)
    assert "_client_stop_cap" not in src, (
        "the decorative per-client scaling helper is back; it cannot bind for "
        f"any n >= 2 because _CLIENT_STOP_SECONDS={main_mod._CLIENT_STOP_SECONDS} "
        f"< 2 * per-client"
    )
    # and the flat slice must still cover ONE stuck client, which is the case
    # that motivated it
    assert main_mod._CLIENT_STOP_SECONDS > 10.0, (
        f"the slice is {main_mod._CLIENT_STOP_SECONDS}s, which does not cover a "
        "single client's 10s internal stop timeout"
    )


@pytest.mark.unit
@pytest.mark.unit
async def test_the_touch_flush_precedes_the_database_close():
    """After db.close() the client rejects writes, so a flush that runs later
    loses every delta with no error."""
    names = [s.name for s in main_mod._teardown_plan()]
    assert names.index("touch buffer") < names.index("database"), (
        "the touch buffer is flushed after the database is closed -- those deltas are lost"
    )


@pytest.mark.unit
async def test_budget_left_never_goes_negative():
    loop = asyncio.get_running_loop()
    assert main_mod._budget_left(loop.time() - 5.0) == 0.0
    assert main_mod._budget_left(loop.time() + 5.0) > 0.0


@pytest.mark.unit
async def test_every_boot_failure_teardown_step_is_bounded_by_the_shared_budget():
    """The four boot-failure handlers are NOT covered by the plan.

    They used to call _safe_teardown_step with no deadline, which grants every
    step its FULL ceiling -- behind a _cancel_tasks allowed 30s on two of them.
    That is an 82s unwind against a 30s grace, and the import-time assert could
    not see it because it only does arithmetic over constants.

    Structural: the alternative is waiting out real timeouts on four code paths
    nobody can exercise cheaply.
    """
    import ast

    source = inspect.getsource(main_mod)
    tree = ast.parse(source)
    source_lines = source.splitlines()

    bounded = 0
    unbounded = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "_safe_teardown_step"
            and node.args
        ):
            continue
        if "deadline" in {kw.arg for kw in node.keywords}:
            bounded += 1
        else:
            # the only call left without a keyword deadline is the plan loop,
            # which passes `deadline` positionally
            if getattr(node.args[0], "attr", None) != "run":
                unbounded.append(f"line {node.lineno}: {source_lines[node.lineno - 1].strip()}")

    assert bounded >= 15, (
        f"only {bounded} teardown steps pass a deadline; the boot-failure paths "
        "should have grown more than that"
    )
    assert not unbounded, (
        "teardown steps with no deadline, so each takes its full ceiling:\n  "
        + "\n  ".join(unbounded)
    )


@pytest.mark.unit
async def test_every_cancellation_timeout_is_bounded_by_a_named_constant():
    """_cancel_tasks RETURNS pending tasks rather than raising, so its timeout is
    fully spendable. Two of the four boot-failure paths allowed a literal 30s,
    which is the entire platform grace period spent before teardown begins."""
    import ast

    tree = ast.parse(inspect.getsource(main_mod))
    timeouts: set = set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_cancel_tasks"):
            continue
        for kw in node.keywords:
            if kw.arg == "timeout":
                timeouts.add(ast.unparse(kw.value))

    assert timeouts, "no _cancel_tasks call found -- the scan is stale"
    for t in timeouts:
        assert t == "_BOOT_FAILURE_CANCEL_SECONDS", (
            f"a cancellation timeout is the literal {t}; literals drift out of the "
            "budget arithmetic, which is how this reached 30s in the first place"
        )
    assert main_mod._BOOT_FAILURE_CANCEL_SECONDS <= 10.0, (
        f"the boot-failure cancel is {main_mod._BOOT_FAILURE_CANCEL_SECONDS}s, which "
        "on its own eats a third of the platform grace period"
    )


@pytest.mark.unit
async def test_the_boot_failure_worst_case_fits_the_grace_period():
    """The module has its own assert on this; recomputing it independently is the
    point, since a shared formula would agree with itself no matter how wrong."""
    REAL_PAAS_GRACE_SECONDS = 30.0
    worst = (
        main_mod._BOOT_FAILURE_CANCEL_SECONDS
        + main_mod._SHUTDOWN_BUDGET_SECONDS
        + main_mod._BOOT_FAILURE_STEP_COUNT * main_mod._TEARDOWN_FLOOR_SECONDS
    )
    assert worst < REAL_PAAS_GRACE_SECONDS, (
        f"a failed boot can take {worst:.1f}s but Heroku, Render and Kubernetes "
        f"only allow {REAL_PAAS_GRACE_SECONDS:.0f}s -- the touch flush and db.close() "
        "would be SIGKILLed on a boot failure"
    )
    assert worst == main_mod._BOOT_FAILURE_WORST_CASE_SECONDS, (
        f"the module computes {main_mod._BOOT_FAILURE_WORST_CASE_SECONDS}s but the "
        f"terms give {worst}s -- one of them has drifted"
    )

    # The count has to match the real thing, or under-counting it makes the
    # bound a comfortable lie. A smaller count still passes the < 30s check,
    # which is why that check alone was not enough.
    import ast

    tree = ast.parse(inspect.getsource(main_mod))
    runs, current = [], 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_safe_teardown_step":
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)

    longest = max(runs) if runs else 0
    assert longest == main_mod._BOOT_FAILURE_STEP_COUNT, (
        f"_BOOT_FAILURE_STEP_COUNT says {main_mod._BOOT_FAILURE_STEP_COUNT} but the "
        f"longest run of teardown steps is {longest}"
    )
    assert (
        len([s for s in main_mod._teardown_plan() if s.shared]) + 2
        == main_mod._BOOT_FAILURE_STEP_COUNT
    ), (
        "the largest boot-failure handler runs the shared plan steps plus the "
        "clients stop and the bot stop"
    )
