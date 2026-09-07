"""M12: unified preflight chain -- gate presets, ordering, fail-closed ids."""

import pytest

from Thunder.utils.decorators import (
    GATES_INFO,
    GATES_STANDARD,
    GATES_START,
    PREFLIGHT_GATES,
    preflight,
)
from Thunder.vars import Var


@pytest.mark.unit
def test_gate_presets_exist_in_registry():
    """Every preset id must resolve in PREFLIGHT_GATES -- a typo'd id is a
    fail-closed rejection in production, and this test fails in CI first."""
    for preset, name in (
        (GATES_STANDARD, "GATES_STANDARD"),
        (GATES_START, "GATES_START"),
        (GATES_INFO, "GATES_INFO"),
    ):
        for gate_id in preset:
            assert gate_id in PREFLIGHT_GATES, f"{name}: unknown gate id {gate_id!r}"


@pytest.mark.unit
def test_gate_presets_match_documented_order():
    # banned -> private-mode -> token (AGENTS.md contract)
    assert GATES_STANDARD == ("banned", "private_mode", "token")
    # /start must stay reachable for token-gated users (no token gate)
    assert GATES_START == ("banned", "private_mode")


@pytest.mark.unit
async def test_unknown_gate_id_rejects_fail_closed():
    """A typo'd gate id must REJECT, never silently skip a security check."""

    class _Msg:
        from_user = None

    assert await preflight(object(), _Msg(), gates=("banned", "typo_gate")) is None
    assert await preflight(object(), _Msg(), gates=("typo_gate",)) is None


@pytest.mark.unit
async def test_preflight_returns_shortener_status_for_owner(monkeypatch):
    """All gates passing returns the shortener status (NOT None) -- the
    False/None contract: callers must use `is None`."""

    class _User:
        id = Var.OWNER_ID

    class _Msg:
        from_user = _User()

    # pin the knob off: with it env-overridden on, the old `or` fallback
    # turned the assertion below into a tautology
    monkeypatch.setattr(Var, "SHORTEN_MEDIA_LINKS", False)
    result = await preflight(object(), _Msg(), gates=GATES_INFO)
    assert result is not None
