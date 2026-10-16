"""Unified preflight chain -- gate presets, ordering, fail-closed ids."""

import pytest

from Thunder.utils.decorators import (
    GATES_STANDARD,
    GATES_START,
    PREFLIGHT_GATES,
    check_banned,
    check_private_mode,
    preflight,
    require_token,
)
from Thunder.vars import Var


@pytest.mark.unit
def test_gate_presets_exist_in_registry():
    """Every preset id must resolve in PREFLIGHT_GATES -- a typo'd id is a
    fail-closed rejection in production, and this test fails in CI first."""
    for preset, name in (
        (GATES_STANDARD, "GATES_STANDARD"),
        (GATES_START, "GATES_START"),
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
    result = await preflight(object(), _Msg(), gates=GATES_START)
    assert result is False


@pytest.mark.unit
async def test_owner_bypasses_force_sub(monkeypatch):
    """Owner skips the entire chain, including force-sub (no RPC made)."""
    import Thunder.utils.force_channel as force_channel_module
    from Thunder.utils.force_channel import force_channel_check

    class _User:
        id = Var.OWNER_ID

    class _Msg:
        from_user = _User()

    calls = {"n": 0}

    async def _boom(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("owner path must not touch the network")

    monkeypatch.setattr(force_channel_module, "_is_member", _boom)
    monkeypatch.setattr(Var, "FORCE_CHANNEL_ID", -100123)
    assert await force_channel_check(object(), _Msg()) is True
    assert calls["n"] == 0


@pytest.mark.unit
async def test_gate_reply_failure_never_breaks_the_gate(monkeypatch):
    """The bot usually has no send permission in a storage channel, so a gate
    denial that cannot be delivered must not change the gate's answer or raise
    -- that is the whole point of routing denials through one helper."""
    from Thunder.utils import decorators as dec

    async def _boom(*args, **kwargs):
        raise RuntimeError("CHAT_WRITE_FORBIDDEN")

    monkeypatch.setattr(dec, "reply_safe", _boom)

    class _Msg:
        from_user = None

    # returns None (no exception escapes) for the failure path...
    assert await dec._reply_ignore_failure(_Msg(), "denied") is None
    # ...and the gate still denies
    assert await dec.check_banned(object(), _Msg()) is False


@pytest.mark.unit
async def test_gate_reply_is_delivered_when_possible(monkeypatch):
    from Thunder.utils import decorators as dec

    seen: list[tuple] = []

    async def _ok(msg, text, **kwargs):
        seen.append((text, kwargs))

    monkeypatch.setattr(dec, "reply_safe", _ok)
    await dec._reply_ignore_failure(object(), "denied", parse_mode="HTML")
    assert seen == [("denied", {"parse_mode": "HTML"})]


@pytest.mark.unit
def test_every_gate_denial_goes_through_the_helper():
    """Guards the refactor: no gate may re-introduce a bare
    try/except Exception: pass around a reply."""
    import inspect

    from Thunder.utils import decorators as dec

    source = inspect.getsource(dec)
    # the helper itself contains the only try/except around a reply
    body = source.split("async def check_banned", 1)[1]
    assert (
        "try:\n" not in body.replace("try:\n            await _reply_ignore_failure", "")
        or body.count("await reply_safe(") == 0
    ), "a gate is calling reply_safe directly instead of the helper"


@pytest.mark.unit
async def test_anonymous_sender_denied_by_every_gate(monkeypatch):
    """Messages with no attributable sender (channel posts, anonymous admins)
    are DENIED fail-closed -- never bypass."""

    class _Msg:
        from_user = None

    # ban gate first in the chain: a banned user must not escape by posting as
    # the channel or as an anonymous admin (AGENTS.md no-sender contract)
    assert await check_banned(object(), _Msg()) is False

    monkeypatch.setattr(Var, "PRIVATE_MODE", True)
    assert await check_private_mode(object(), _Msg()) is False

    monkeypatch.setattr(Var, "TOKEN_ENABLED", True)
    assert await require_token(object(), _Msg()) is False
