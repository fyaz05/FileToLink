"""The per-user bookkeeping bound: admission must enforce it, and eviction must
not hand an active user a fresh window.

Both properties were checked as WIRING and found wrong. A test read the constant
and compared numbers; nothing ever filled the table or checked whose window got
reset. That is the recurring shape of the false-confidence defect in this repo: a
fake, or an assertion, that re-decides what the real code decides.
"""

import time
from collections import deque

import pytest

from Thunder.utils import rate_limiter as rl_mod
from Thunder.utils.rate_limiter import rate_limiter

pytestmark = pytest.mark.unit


@pytest.fixture
def limiter(monkeypatch):
    """A fresh limiter, so these tests do not inherit the singleton's state."""
    fresh = rl_mod.RateLimiter()
    fresh.enabled = True
    fresh._initialization_error = False
    fresh.global_rate_limit_enabled = False
    fresh.max_requests_per_period = 5
    fresh.rate_limit_period_seconds = 3600
    fresh.user_requests.clear()
    yield fresh
    fresh.user_requests.clear()


@pytest.mark.unit
async def test_admission_never_exceeds_the_tracked_user_bound(limiter, monkeypatch):
    """A sweep on a timer bounds the steady state, not what can be created
    between sweeps. Only admission makes it a real bound."""
    monkeypatch.setattr(rl_mod, "MAX_TRACKED_USERS", 50)

    for i in range(500):
        await limiter.check_limits(i, record=True)

    assert len(limiter.user_requests) <= 50, (
        f"{len(limiter.user_requests)} user windows tracked against a cap of 50 "
        "-- admission is not enforcing the bound"
    )


@pytest.mark.unit
async def test_eviction_does_not_reset_a_currently_active_users_window(limiter, monkeypatch):
    """This is the reason eviction sorts by recency.

    Keys are only inserted when absent, so dict order is FIRST-EVER-SEEN order.
    Evicting by it drops whoever joined longest ago -- which, for a long-lived
    account that keeps using the bot, is whoever is CURRENTLY ACTIVE. They are
    silently handed a fresh window and get another full allowance.

    The scenario needs the victim to be both first-inserted AND still active. A
    victim that has gone idle is legitimately the least-recently-active entry
    and dropping it is correct -- so the victim keeps a current timestamp while
    new ids churn through admission.
    """
    monkeypatch.setattr(rl_mod, "MAX_TRACKED_USERS", 20)
    # headroom, so the victim can keep touching without filling its own window
    limiter.max_requests_per_period = 500

    victim = 1
    for _ in range(5):
        assert await limiter.check_limits(victim, record=True) is True

    for i in range(2, 400):
        await limiter.check_limits(i, record=True)
        # still active: a request every round keeps its window current
        await limiter.check_limits(victim, record=True)

    assert victim in limiter.user_requests, (
        "a currently active user was evicted by id churn -- their rate-limit "
        "window was reset by an unrelated flood of new ids"
    )
    assert len(limiter.user_requests[victim]) == 5 + 398, (
        f"the victim's window was truncated to {len(limiter.user_requests[victim])} "
        "-- their accumulated allowance was partly forgotten"
    )


@pytest.mark.unit
async def test_eviction_prefers_the_least_recently_active(limiter, monkeypatch):
    """Directly: the victim is the entry with the oldest newest timestamp."""
    monkeypatch.setattr(rl_mod, "MAX_TRACKED_USERS", 3)
    now = time.time()

    # insert in an order that is NOT recency order: sleepiest first
    limiter.user_requests[10] = deque([now - 900])
    limiter.user_requests[11] = deque([now - 800])
    limiter.user_requests[12] = deque([now - 100])  # most recent

    limiter._evict_inactive_users(2)

    assert 12 in limiter.user_requests, "the most recently active entry was evicted"
    assert 11 in limiter.user_requests, "the second most recent was evicted"
    assert 10 not in limiter.user_requests, "the least recently active entry survived the eviction"


@pytest.mark.unit
async def test_the_sweep_and_admission_agree_on_who_goes(limiter, monkeypatch):
    """One helper, so the two paths cannot drift again."""
    monkeypatch.setattr(rl_mod, "MAX_TRACKED_USERS", 4)
    now = time.time()

    def seed():
        limiter.user_requests.clear()
        for i, age in enumerate([500, 400, 300, 200, 100]):
            limiter.user_requests[i] = deque([now - age])

    seed()
    limiter._evict_inactive_users(4)
    by_admission = set(limiter.user_requests)

    seed()
    await limiter.sweep()
    by_sweep = set(limiter.user_requests)

    assert by_sweep, "the sweep expired everything -- the fixture timestamps are stale"
    assert by_admission == by_sweep, (
        f"admission kept {by_admission} but the sweep kept {by_sweep} -- the two "
        "eviction paths disagree about whose window to drop"
    )
    assert len(by_sweep) <= 4


@pytest.mark.unit
async def test_eviction_is_one_pass_not_one_per_victim(limiter, monkeypatch):
    """O(n*k) vs O(n log n), measured rather than asserted about.

    The min()-per-victim version was functionally correct -- every behavioural
    test here passed with it -- and took 66 s of blocking CPU to evict 28672
    entries, inside an async sweep() the sweeper calls directly. So the whole
    bot freezes for a minute, under exactly the id-flood this bound exists to
    survive. No functional assertion can see that; only the clock can.

    The budget is 2 s against a correct runtime of ~0.05 s, so it is nowhere
    near the point where a loaded CI box could fail it, while the O(n*k) form
    is 30x over.
    """
    import time

    monkeypatch.setattr(rl_mod, "MAX_TRACKED_USERS", 4096)
    now = time.time()
    for i in range(4096 * 8):
        limiter.user_requests[i] = deque([now - (4096 * 8 - i) * 0.001])

    started = time.perf_counter()
    dropped = limiter._evict_inactive_users(4096)
    elapsed = time.perf_counter() - started

    assert dropped == 4096 * 7, f"evicted {dropped}, expected {4096 * 7}"
    assert len(limiter.user_requests) == 4096
    assert elapsed < 2.0, (
        f"evicting {4096 * 7} entries took {elapsed:.1f}s -- that is the O(n*k) "
        "min()-per-victim form, and sweep() runs it on the event loop"
    )


@pytest.mark.unit
async def test_the_eviction_body_has_no_per_victim_loop():
    """The same property structurally, so it is pinned even if the clock test
    is skipped or the table happens to be small on some host.

    Parsed rather than grepped: a naive "while" in the source also matches the
    word in the docstring.
    """
    import ast
    import inspect

    func = next(
        n
        for n in ast.walk(ast.parse(inspect.getsource(rl_mod)))
        if isinstance(n, ast.FunctionDef) and n.name == "_evict_inactive_users"
    )
    loops = [n for n in ast.walk(func) if isinstance(n, (ast.While, ast.comprehension))]
    assert not loops, (
        f"_evict_inactive_users contains {len(loops)} loop(s) at lines "
        f"{[n.lineno for n in loops]}; it must sort once and drop the prefix"
    )
    sorts = [
        n
        for n in ast.walk(func)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "sorted"
    ]
    assert len(sorts) == 1, f"expected exactly one sorted() call, found {len(sorts)}"


@pytest.mark.unit
async def test_the_singleton_is_actually_using_the_constant(limiter, monkeypatch):
    """MAX_TRACKED_USERS was defined twice: a new 50_000 immediately shadowed by
    the pre-existing 4096 that sweep() uses. Ruff's F811 does not flag a plain
    module-level rebinding, so the blocking lint gate stayed green over dead
    code that documented a bound nothing used."""
    import inspect

    source = inspect.getsource(rl_mod)
    assert source.count("MAX_TRACKED_USERS =") == 1, (
        "MAX_TRACKED_USERS is assigned more than once -- the later binding wins "
        "and the earlier one is dead code"
    )
    assert rate_limiter is not None
