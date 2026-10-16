"""Fairness between the priority and regular queues.

A continuous stream of owner/authorized requests used to starve regular users
indefinitely: the drain took the priority queue whenever it was non-empty. The
position estimate was honest about the wait, but nothing bounded it. The drain
now alternates while both tiers are populated.
"""

import time

import pytest

from Thunder.utils.rate_limiter import TokenBucket, rate_limiter

UID_REG = 90_501
UID_PRIO = 90_502


def _arm(monkeypatch, ran: list[int]) -> None:
    rl = rate_limiter
    rl.enabled = True
    rl._initialization_error = False
    rl.max_requests_per_period = 10_000
    rl.rate_limit_period_seconds = 60
    rl.global_rate_limit_enabled = False
    rl._prefer_regular = False
    monkeypatch.setattr(rl, "is_owner", lambda user_id: False)
    monkeypatch.setattr(rl, "breaker", TokenBucket(rate_per_second=10_000.0, burst_multiplier=2.0))
    for uid in (UID_REG, UID_PRIO):
        rl.user_requests.pop(uid, None)
    rl.request_queue.clear()
    rl.priority_queue.clear()
    return rl


def _item(uid: int, ran: list[int]) -> dict:
    async def handler(*args, **kwargs):
        ran.append(uid)

    return {
        "user_id": uid,
        "func": handler,
        "args": (),
        "kwargs": {},
        "file_identifier": None,
        "not_before": 0.0,
        "charged": True,  # already charged: these tests are about ordering only
    }


def _fill(rl, uid: int, count: int, ran: list[int], *, queue: str) -> None:
    target = rl.priority_queue if queue == "priority" else rl.request_queue
    for _ in range(count):
        target.append(_item(uid, ran))


@pytest.mark.unit
async def test_priority_only_is_drained_completely(monkeypatch):
    ran: list[int] = []
    rl = _arm(monkeypatch, ran)
    _fill(rl, UID_PRIO, 3, ran, queue="priority")

    for _ in range(3):
        assert await rl._process_one() is True

    assert not rl.priority_queue
    assert ran == [UID_PRIO] * 3
    assert not rl.request_queue


@pytest.mark.unit
async def test_regular_only_is_drained_completely(monkeypatch):
    ran: list[int] = []
    rl = _arm(monkeypatch, ran)
    _fill(rl, UID_REG, 3, ran, queue="regular")

    for _ in range(3):
        assert await rl._process_one() is True

    assert ran == [UID_REG] * 3
    assert not rl.priority_queue


@pytest.mark.unit
async def test_a_continuous_priority_stream_cannot_starve_regulars(monkeypatch):
    """A priority item is refilled before every pop, so priority is *never*
    empty -- which is exactly the condition under which the old
    priority-first drain starved regulars indefinitely. Every regular request
    must still run."""
    ran: list[int] = []
    rl = _arm(monkeypatch, ran)
    _fill(rl, UID_PRIO, 3, ran, queue="priority")
    _fill(rl, UID_REG, 3, ran, queue="regular")

    served_regular = 0
    for _ in range(12):
        assert await rl._process_one() is True
        if ran and ran[-1] == UID_REG:
            served_regular += 1
        # keep the priority queue permanently non-empty
        rl.priority_queue.append(_item(UID_PRIO, ran))

    assert served_regular == 3, f"regular requests starved ({served_regular}/3 served)"
    assert not rl.request_queue


@pytest.mark.unit
async def test_alternation_starts_with_priority(monkeypatch):
    """The owner keeps its edge on ties -- only the starvation bound changes."""
    ran: list[int] = []
    rl = _arm(monkeypatch, ran)
    rl.priority_queue.append(_item(UID_PRIO, ran))
    rl.request_queue.append(_item(UID_REG, ran))

    assert await rl._process_one() is True
    assert ran == [UID_PRIO]

    assert await rl._process_one() is True
    assert ran == [UID_PRIO, UID_REG]


@pytest.mark.unit
async def test_deferred_regular_item_is_rotated_not_lost(monkeypatch):
    """A parked regular item comes back to the queue instead of blocking
    priority work or vanishing."""
    ran: list[int] = []
    rl = _arm(monkeypatch, ran)
    deferred = _item(UID_REG, ran)
    deferred["not_before"] = time.time() + 3600
    rl.request_queue.append(deferred)
    rl.priority_queue.append(_item(UID_PRIO, ran))

    assert await rl._process_one() is True  # tie -> priority
    assert await rl._process_one() is True  # regular, not runnable, rotated

    assert ran == [UID_PRIO]  # the deferred item was not executed
    assert len(rl.request_queue) == 1, "deferred item was lost"
    assert not rl.priority_queue
    assert rl._prefer_regular is False  # toggle resets when a tier empties


@pytest.mark.unit
async def test_fairness_toggle_resets_when_a_tier_empties(monkeypatch):
    ran: list[int] = []
    rl = _arm(monkeypatch, ran)
    rl.priority_queue.append(_item(UID_PRIO, ran))
    rl.request_queue.append(_item(UID_REG, ran))

    await rl._process_one()  # priority (toggle flips to prefer-regular)
    await rl._process_one()  # regular, both tiers now empty
    assert rl._prefer_regular is False
