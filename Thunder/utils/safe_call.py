# Thunder/utils/safe_call.py

"""Central FloodWait-safe call helpers.

Every Telegram RPC in the codebase goes through :func:`tg_call` or one of the
thin wrappers below instead of the historical copy-pasted
``try/except FloodWait`` pairs.  Semantics preserved from the old pattern:

* on ``FloodWait`` the coroutine sleeps for ``min(e.value, MAX_FLOODWAIT_SLEEP_SECONDS)``
  seconds and retries, at most ``retries`` times (default 1 -- i.e. two
  attempts total, matching the previous inline behaviour); Telegram can
  send multi-minute FloodWaits, and an uncapped sleep let a "lightweight"
  RPC pin its caller far beyond the wall-clock budget it advertises (H8);
* after the retries are exhausted the exception propagates unchanged.

Wall-clock budgets (H8): lightweight RPCs get a default timeout so a hung
call can never pin a handler forever.  File-transfer paths (copy / upload /
download) default to *no* timeout because large media legitimately takes
minutes; pass ``timeout=`` explicitly where a budget is known.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pyrogram.errors import FloodWait

from Thunder.utils.logger import logger

T = TypeVar("T")

# Default wall-clock budget for lightweight RPCs (get_me, get_messages,
# edit_text, answer, ...).  Env-overridable via TG_RPC_TIMEOUT_SECONDS.
DEFAULT_RPC_TIMEOUT_SECONDS = 30.0

# H8: cap a single FloodWait sleep so a lightweight RPC cannot exceed its
# advertised budget by minutes.  Total sleep is also bounded by ``retries``.
MAX_FLOODWAIT_SLEEP_SECONDS = 30.0

# Call shapes that are allowed to run unbounded by default (large media
# transfers).  Matched by attribute name of the callable.
_UNBOUNDED_SHAPES = {
    "copy",
    "copy_message",
    "send_document",
    "send_video",
    "send_audio",
    "send_photo",
    "send_animation",
    "send_voice",
    "send_video_note",
    "reply_document",
    "reply_video",
    "reply_photo",
    "reply_audio",
    "stream_media",
    "download_media",
    "send_cached_media",
}


_env_timeout_cache: float | None = None


def _env_timeout() -> float:
    # TG_RPC_TIMEOUT_SECONDS is static per process; resolve it once instead
    # of re-importing + re-reading on every RPC.
    global _env_timeout_cache
    if _env_timeout_cache is None:
        try:
            from Thunder.vars import Var  # lazy: avoids any import-order coupling

            _env_timeout_cache = float(Var.TG_RPC_TIMEOUT_SECONDS)
        except Exception:
            _env_timeout_cache = DEFAULT_RPC_TIMEOUT_SECONDS
    return _env_timeout_cache


def _default_timeout(fn: Callable[..., Awaitable[T]]) -> float | None:
    if getattr(fn, "__name__", "") in _UNBOUNDED_SHAPES:
        return None
    return _env_timeout()


async def tg_call(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    retries: int = 1,
    timeout: float | None = None,
    on_error: Callable[[Exception], None] | None = None,
    **kwargs: Any,
) -> T:
    """Call ``fn(*args, **kwargs)`` sleeping through ``FloodWait``.

    ``timeout`` forces a wall-clock budget (``None`` = auto: unbounded for
    file-transfer shapes, :data:`DEFAULT_RPC_TIMEOUT_SECONDS` otherwise;
    ``0`` or negative disables the budget entirely).
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
            sleep_for = min(e.value, MAX_FLOODWAIT_SLEEP_SECONDS)
            logger.debug(
                f"FloodWait in {getattr(fn, '__name__', fn)}, "
                f"sleeping {sleep_for}s (asked {e.value}s, attempt {attempt}/{retries})"
            )
            await asyncio.sleep(sleep_for)
        except Exception as e:
            if on_error is not None:
                try:
                    on_error(e)
                except Exception:
                    logger.debug("on_error hook raised", exc_info=True)
            raise


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
    "DEFAULT_RPC_TIMEOUT_SECONDS",
    "MAX_FLOODWAIT_SLEEP_SECONDS",
]
