"""Central FloodWait-safe call helpers.

Every Telegram RPC goes through :func:`tg_call` or a thin wrapper.  On
``FloodWait`` the call sleeps ``min(e.value, cap)`` -- where the cap is
30 s for lightweight RPCs and :data:`MAX_FLOODWAIT_SLEEP_MEDIA_SECONDS`
(600 s, the pre-branch pyrogram auto-sleep ceiling) for file-transfer
shapes -- and retries at most ``retries`` times, then the exception
propagates unchanged.  Wall-clock budgets (H8): lightweight RPCs get a
default timeout so a hung call cannot pin a handler forever;
file-transfer paths default to *no* timeout (large media legitimately
takes minutes) -- pass ``timeout=`` explicitly where a budget is known.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pyrogram.errors import FloodWait

from Thunder.utils.logger import logger

T = TypeVar("T")

# Default wall-clock budget for lightweight RPCs; env-overridable via TG_RPC_TIMEOUT_SECONDS.
DEFAULT_RPC_TIMEOUT_SECONDS = 30.0

# cap FloodWait sleeps so a lightweight RPC stays near its advertised wall-clock
# budget (worst case per attempt: budget + cap + a final budgeted attempt).
MAX_FLOODWAIT_SLEEP_SECONDS = 30.0

# File-transfer shapes may sleep the full FloodWait up to this ceiling (the
# pyrogram auto-sleep budget): sustained throttling on the ingest path must
# ride out long waits instead of hard-failing; beyond it, surface to caller.
MAX_FLOODWAIT_SLEEP_MEDIA_SECONDS = 600.0

# Media shapes allowed to run unbounded (large transfers legitimately take
# minutes, and H4a's lightweight budget would abort them mid-upload).
#
# Both entries are load-bearing and correspond to real call sites:
#   * Message.copy      -- the ingest forward (plugins fwd_media) and the
#                          broadcast copy
#   * Message.reply_document -- /log (a 5 MiB tail) and /shell output
#
# Everything else that moves media -- stream_media, download_media, the
# send_* family -- is called directly, not via tg_call, so it has no budget
# to inherit. A new media call site must be added here or pass ``timeout=0``;
# silently inheriting the lightweight budget is a production upload failure.
# test_safe_call_budgets pins this set.
UNBOUNDED_SHAPES = frozenset({"copy", "reply_document"})


def _call_name(fn: Callable[..., Awaitable[T]]) -> str:
    """Name used to pick a budget.

    ``functools.partial`` hides the wrapped callable behind ``func``; without
    this a partial-wrapped media call would look unnamed and silently inherit
    the lightweight budget.
    """
    wrapped = getattr(fn, "func", None)  # functools.partial
    if wrapped is not None:
        fn = wrapped
    return getattr(fn, "__name__", "")


_env_timeout_cache: float | None = None


def _env_timeout() -> float:
    # static per process: resolve once instead of re-reading on every RPC
    global _env_timeout_cache
    if _env_timeout_cache is None:
        try:
            from Thunder.vars import Var  # lazy: avoids any import-order coupling

            _env_timeout_cache = float(Var.TG_RPC_TIMEOUT_SECONDS)
        except Exception:
            _env_timeout_cache = DEFAULT_RPC_TIMEOUT_SECONDS
    return _env_timeout_cache


def _default_timeout(fn: Callable[..., Awaitable[T]]) -> float | None:
    if _call_name(fn) in UNBOUNDED_SHAPES:
        return None
    return _env_timeout()


async def tg_call(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    retries: int = 1,
    timeout: float | None = None,
    max_flood_sleep: float | None = None,
    **kwargs: Any,
) -> T:
    """Call ``fn(*args, **kwargs)`` sleeping through ``FloodWait``.

    ``timeout`` forces a wall-clock budget (``None`` = auto: unbounded for the
    :data:`UNBOUNDED_SHAPES` media call, :data:`DEFAULT_RPC_TIMEOUT_SECONDS`
    otherwise; ``0`` or negative disables the budget entirely, which is how a
    new media call site opts out explicitly). ``max_flood_sleep`` overrides the
    shape-based sleep cap (e.g. fan-out callers that must fail-count fast
    instead of riding out a long throttle).
    """
    attempt = 0
    while True:
        try:
            budget = timeout if timeout is not None else _default_timeout(fn)
            coro = fn(*args, **kwargs)
            if budget and budget > 0:
                return await asyncio.wait_for(coro, timeout=budget)
            return await coro
        except FloodWait as e:
            attempt += 1
            if attempt > retries:
                raise
            if max_flood_sleep is not None:
                cap = max_flood_sleep
            else:
                cap = (
                    MAX_FLOODWAIT_SLEEP_MEDIA_SECONDS
                    if _call_name(fn) in UNBOUNDED_SHAPES
                    else MAX_FLOODWAIT_SLEEP_SECONDS
                )
            sleep_for = min(e.value, cap)
            logger.debug(
                f"FloodWait in {_call_name(fn) or fn}, "
                f"sleeping {sleep_for}s (asked {e.value}s, attempt {attempt}/{retries})"
            )
            await asyncio.sleep(sleep_for)
        # every other exception propagates unchanged (fail-closed callers
        # classify the error; broadcast counts it; routes map it)


async def reply_safe(msg: Any, text: str, retries: int = 1, **kwargs: Any):
    return await tg_call(msg.reply_text, text, quote=True, retries=retries, **kwargs)


async def send_safe(cli: Any, chat_id: Any, retries: int = 1, **kwargs: Any):
    return await tg_call(cli.send_message, chat_id=chat_id, retries=retries, **kwargs)


async def edit_safe(msg: Any, text: str, retries: int = 1, **kwargs: Any):
    return await tg_call(msg.edit_text, text, retries=retries, **kwargs)


async def delete_safe(msg: Any, retries: int = 1):
    return await tg_call(msg.delete, retries=retries)


async def answer_safe(query: Any, text: str = "", retries: int = 1, **kwargs: Any):
    return await tg_call(query.answer, text, retries=retries, **kwargs)


__all__ = [
    "tg_call",
    "reply_safe",
    "send_safe",
    "edit_safe",
    "delete_safe",
    "answer_safe",
    "UNBOUNDED_SHAPES",
    "DEFAULT_RPC_TIMEOUT_SECONDS",
    "MAX_FLOODWAIT_SLEEP_SECONDS",
    "MAX_FLOODWAIT_SLEEP_MEDIA_SECONDS",
]
