"""The legacy /watch preview must not be an unauthenticated Telegram RPC
amplifier.

render_page() fetches the vault message BEFORE it can verify the capability
hash -- the hash is a prefix of the file unique id stored in the very message
being fetched, so verification is necessarily downstream of the fetch. A
request whose hash does not match can therefore never be cached, and every
shape-valid /watch URL from an unauthenticated caller is a fresh RPC against
the bot account.

The branch added admission control to /f/ (work_loads + select_optimal_client)
but nothing to /watch/. These tests pin the bound.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from aiohttp import web

from Thunder.utils import render_template as rt
from Thunder.utils.render_template import render_page

pytestmark = pytest.mark.unit


@pytest.fixture
def reset_admission():
    rt._preview_rpc_inflight = 0
    yield
    rt._preview_rpc_inflight = 0


@pytest.fixture
def no_cache():
    rt._legacy_cache.clear()
    yield
    rt._legacy_cache.clear()


# --- the guard itself ------------------------------------------------------


def test_concurrency_limit_is_bounded():
    assert 0 < rt._PREVIEW_RPC_CONCURRENCY <= 16, (
        "unbounded means unbounded; a large value means the amplifier still works"
    )


def test_slot_sheds_with_a_retryable_503():
    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    try:
        with pytest.raises(web.HTTPServiceUnavailable) as excinfo:
            with rt._preview_rpc_slot():
                pass
        assert excinfo.value.headers["Retry-After"]
        assert excinfo.value.status == 503
    finally:
        rt._preview_rpc_inflight = 0


def test_slot_is_released_on_success_and_on_failure(reset_admission):
    with rt._preview_rpc_slot():
        assert rt._preview_rpc_inflight == 1
    assert rt._preview_rpc_inflight == 0, "a slot leaked after a normal exit"

    with pytest.raises(RuntimeError):
        with rt._preview_rpc_slot():
            raise RuntimeError("rpc blew up")
    assert rt._preview_rpc_inflight == 0, "a slot leaked after an exception"


async def test_slot_is_released_on_cancellation(reset_admission):
    async def run():
        with rt._preview_rpc_slot():
            await asyncio.sleep(3600)

    task = asyncio.ensure_future(run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rt._preview_rpc_inflight == 0, (
        "a cancelled preview held its slot forever -- one abort would then "
        "permanently consume capacity"
    )


async def test_slots_are_bounded_under_concurrency(reset_admission):
    """Peak in-flight never exceeds the cap, however many arrive at once."""
    peak = 0
    admitted = 0
    shed = 0

    async def one():
        nonlocal peak, admitted, shed
        try:
            with rt._preview_rpc_slot():
                admitted += 1
                peak = max(peak, rt._preview_rpc_inflight)
                await asyncio.sleep(0)
        except web.HTTPServiceUnavailable:
            shed += 1

    await asyncio.gather(*(one() for _ in range(40)))
    assert peak <= rt._PREVIEW_RPC_CONCURRENCY
    assert admitted == rt._PREVIEW_RPC_CONCURRENCY, (
        f"{admitted} concurrent RPCs were admitted, cap is {rt._PREVIEW_RPC_CONCURRENCY}"
    )
    assert shed == 40 - rt._PREVIEW_RPC_CONCURRENCY


# --- the guard is actually on the RPC path ---------------------------------


async def test_cold_render_passes_through_the_admission_slot(
    reset_admission, no_cache, monkeypatch
):
    """The slot must wrap the vault fetch. A cache hit costs no RPC and so
    must not consume a slot."""
    from Thunder.server.exceptions import InvalidHash

    calls = []
    inflight_during_rpc = []

    async def fake_tg_call(fn, *args, **kwargs):
        calls.append(kwargs.get("message_ids"))
        # observed from inside the RPC: asserts the slot is on THIS path, not
        # merely released afterwards (which is also true if never taken)
        inflight_during_rpc.append(rt._preview_rpc_inflight)
        return None  # -> InvalidHash, i.e. a cache miss with no cache write

    monkeypatch.setattr(rt, "tg_call", fake_tg_call)

    with pytest.raises(InvalidHash):
        await render_page(12345, "abcdef")

    assert calls == [12345], "the vault fetch was not made"
    assert inflight_during_rpc == [1], (
        "the vault RPC ran outside the admission slot -- the guard is "
        "defined but not wired onto the amplifier"
    )
    assert rt._preview_rpc_inflight == 0, "the slot was not released after the fetch"


async def test_a_shed_request_never_reaches_telegram(reset_admission, no_cache, monkeypatch):
    """The point of the guard: a shed caller must cost zero RPCs."""
    from Thunder.server.exceptions import InvalidHash  # noqa: F401

    calls = []

    async def fake_tg_call(fn, *args, **kwargs):
        calls.append(1)
        return None

    monkeypatch.setattr(rt, "tg_call", fake_tg_call)

    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    try:
        for i in range(10):
            with pytest.raises(web.HTTPServiceUnavailable):
                await render_page(1000 + i, "abcdef")
    finally:
        rt._preview_rpc_inflight = 0

    assert not calls, f"{len(calls)} Telegram RPCs were spent while shedding"


async def test_cached_render_does_not_consume_a_slot(reset_admission, no_cache, monkeypatch):
    rt._legacy_cache_put((7, "aabbcc"), "v.mp4", "video/mp4", 1024)

    async def fake_tg_call(*args, **kwargs):
        raise AssertionError("a cached render must not spend an RPC")

    monkeypatch.setattr(rt, "tg_call", fake_tg_call)

    # saturate the gate first: a cache hit costs no RPC, so it must not be
    # shed just because other callers are busy
    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    out = await render_page(7, "aabbcc")
    assert "v.mp4" in out
    assert rt._preview_rpc_inflight == rt._PREVIEW_RPC_CONCURRENCY, (
        "a cache hit took a slot from the RPC budget"
    )


async def test_load_shed_is_not_logged_as_an_error(reset_admission, no_cache, monkeypatch, caplog):
    """A shed request is the system working, not a fault: a traceback per
    rejected request under attack is its own outage."""
    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    try:
        with pytest.raises(web.HTTPServiceUnavailable):
            await render_page(9, "abcdef")
    finally:
        rt._preview_rpc_inflight = 0
    assert not [r for r in caplog.records if r.levelname == "ERROR"], (
        "load shedding was logged as an error"
    )


async def test_a_shed_response_carries_no_store(reset_admission, no_cache, monkeypatch):
    """A cached 503 outlives the condition that produced it. Every other error
    the server emits goes through server.exceptions.unavailable; a hand-rolled
    header set is how this one ended up without Cache-Control."""
    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    try:
        with pytest.raises(web.HTTPServiceUnavailable) as excinfo:
            await render_page(9, "abcdef")
    finally:
        rt._preview_rpc_inflight = 0

    headers = excinfo.value.headers
    assert headers.get("Cache-Control") == "no-store", (
        "the shed response can be cached by a CDN or browser"
    )
    assert headers.get("Retry-After"), "no Retry-After, so clients will not back off"
    assert headers.get("Access-Control-Allow-Origin") == "*", (
        "a browser cannot read the failure reason"
    )


async def test_shed_headers_match_the_routes_error_headers(reset_admission, no_cache, monkeypatch):
    """Structural: the shed and the routes must agree, so the set lives in one
    place (server.exceptions) rather than being retyped at each call site."""
    from Thunder.server.exceptions import ERROR_HEADERS

    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    try:
        with pytest.raises(web.HTTPServiceUnavailable) as excinfo:
            await render_page(9, "abcdef")
    finally:
        rt._preview_rpc_inflight = 0

    for name, value in ERROR_HEADERS.items():
        assert excinfo.value.headers.get(name) == value, f"shed response differs on {name}"


async def test_the_route_ladder_does_not_log_one_line_per_shed_request(
    reset_admission, no_cache, monkeypatch, caplog
):
    """The flood only moved from ERROR to WARNING. An attacker who can trigger
    the shed can still fill the log file that /log uploads, so nothing at
    WARNING or above may be emitted for a 5xx."""
    from Thunder.server import stream_routes

    rt._preview_rpc_inflight = rt._PREVIEW_RPC_CONCURRENCY
    try:
        with pytest.raises(web.HTTPException):
            async with stream_routes._route_ladder("preview"):
                await render_page(9, "abcdef")
    finally:
        rt._preview_rpc_inflight = 0

    noisy = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not noisy, f"a shed request logged {len(noisy)} record(s) at WARNING or above: {noisy}"


async def test_the_route_ladder_still_logs_client_errors(monkeypatch, caplog):
    """4xx is a client mistake and is worth a line; only 5xx is shed."""
    from Thunder.server import stream_routes

    with pytest.raises(web.HTTPException):
        async with stream_routes._route_ladder("preview"):
            raise web.HTTPBadRequest(text="nope")

    assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a 4xx was swallowed silently -- that is a real client mistake"
    )


async def test_a_wrong_hash_is_a_404_not_a_logged_error(
    reset_admission, no_cache, monkeypatch, caplog
):
    """Same amplifier, aimed at bot.txt: one ERROR traceback per probe is how
    an unauthenticated caller fills the log file that /log uploads."""
    from Thunder.server.exceptions import InvalidHash

    async def fake_tg_call(fn, *args, **kwargs):
        return SimpleNamespace(id=1, unique_id="zzzzzz-unique", document=None)

    monkeypatch.setattr(rt, "tg_call", fake_tg_call)

    for _ in range(5):
        with pytest.raises(InvalidHash):
            await render_page(1, "abcdef")

    errors = [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL")]
    assert not errors, f"{len(errors)} of 5 wrong-hash probes were logged as errors"


async def test_a_wrong_hash_is_not_cacheable_so_the_bound_matters(
    reset_admission, no_cache, monkeypatch
):
    """Why concurrency control is the only pre-fetch defence: a mismatched
    hash leaves nothing in the cache, so a repeated attack is a repeated RPC."""
    from Thunder.server.exceptions import InvalidHash

    calls = []

    async def fake_tg_call(fn, *args, **kwargs):
        calls.append(1)
        return SimpleNamespace(id=1, unique_id="zzzzzz-unique", document=None)

    monkeypatch.setattr(rt, "tg_call", fake_tg_call)

    for _ in range(3):
        with pytest.raises(InvalidHash):
            await render_page(1, "abcdef")
    assert len(calls) == 3, "a rejected hash was cached, hiding the amplifier"
    assert not rt._legacy_cache
