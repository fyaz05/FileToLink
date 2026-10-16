import contextlib
import time
from collections import OrderedDict
from pathlib import Path

from aiohttp import web
from jinja2 import Environment, FileSystemLoader, select_autoescape

from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.file_properties import get_fname, get_fsize, get_media, get_uniqid
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.utils.media_types import ext_and_mime_for_class
from Thunder.utils.safe_call import tg_call
from Thunder.vars import Var

# NOTE: lazy import of Thunder.server.exceptions inside render_page() avoids
# a circular import (server/__init__ -> stream_routes -> here).

# resolve templates relative to the package, not the process CWD.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"

template_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=True),
    enable_async=True,
    cache_size=200,
    auto_reload=False,
    optimized=True,
)


def _page_kind(mime_type: str | None, file_name: str) -> str:
    """Typed player page -- derive the layout from the mime type.

    A specific mime type is authoritative; extension sniffing only applies
    when the mime type is missing or generic (application/octet-stream).
    """
    mime = (mime_type or "").lower()
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime and mime != "application/octet-stream":
        return "other"
    # fall back to extension sniffing when the mime type is missing/generic
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext in {"mp3", "m4a", "ogg", "opus", "wav", "flac", "aac"}:
        return "audio"
    if ext in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return "image"
    if ext in {"mp4", "mkv", "webm", "mov", "avi", "m4v"}:
        return "video"
    return "other"


async def render_media_page(
    file_name: str,
    src: str,
    mime_type: str | None = None,
    size_bytes: int | None = None,
) -> str:
    # NOTE: src must be a pre-encoded URL. Templates use |safe to avoid double-encoding.
    template = template_env.get_template("req.html")
    context = {
        "heading": f"View {file_name}",
        "file_name": file_name,
        "src": f"{src}?disposition=inline",
        "download_src": f"{src}?disposition=attachment",
        "kind": _page_kind(mime_type, file_name),
        "mime_type": mime_type or "application/octet-stream",
        "size_formatted": humanbytes(size_bytes) if size_bytes else None,
    }
    return await template.render_async(**context)


# TTL+LRU cache so repeat legacy /watch views don't re-fetch the vault message.
_legacy_cache: "OrderedDict[tuple[int, str], tuple[float, str, str | None, int]]" = OrderedDict()
_LEGACY_CACHE_TTL_SECONDS = 600
_LEGACY_CACHE_MAX_ITEMS = 1024

# Admission control for the vault lookup in render_page().
#
# The capability hash is a prefix of the file unique id stored in the very
# message we must fetch to check it, so the RPC necessarily happens BEFORE the
# hash is verified. A request whose hash does not match can never be cached,
# so an unauthenticated caller holding one shape-valid 6-hex hash can turn
# every request into a fresh Telegram RPC -- an amplifier, not a lookup.
#
# A plain counter, not asyncio.Semaphore: the check and the increment have no
# await between them, so asyncio cannot interleave, and a module-level
# Semaphore would bind to the first event loop that touches it and then reject
# every other loop (pytest-asyncio creates one per test).
_PREVIEW_RPC_CONCURRENCY = 4
_preview_rpc_inflight = 0


@contextlib.contextmanager
def _preview_rpc_slot():
    global _preview_rpc_inflight
    if _preview_rpc_inflight >= _PREVIEW_RPC_CONCURRENCY:
        raise web.HTTPServiceUnavailable(
            text="Server is busy rendering pages. Please retry shortly.",
            headers={
                "Retry-After": "2",
                "Access-Control-Allow-Origin": "*",
                "X-Content-Type-Options": "nosniff",
                "X-Robots-Tag": "noindex, nofollow",
            },
        )
    _preview_rpc_inflight += 1
    try:
        yield
    finally:
        _preview_rpc_inflight -= 1


def _legacy_cache_get(key) -> tuple[str, str | None, int] | None:
    cached = _legacy_cache.get(key)
    if not cached:
        return None
    ts, file_name, mime_type, size_bytes = cached
    if time.monotonic() - ts > _LEGACY_CACHE_TTL_SECONDS:
        _legacy_cache.pop(key, None)
        return None
    _legacy_cache.move_to_end(key)
    return file_name, mime_type, size_bytes


def _legacy_cache_put(
    key, file_name: str, mime_type: str | None = None, size_bytes: int = 0
) -> None:
    _legacy_cache[key] = (time.monotonic(), file_name, mime_type, size_bytes)
    _legacy_cache.move_to_end(key)
    while len(_legacy_cache) > _LEGACY_CACHE_MAX_ITEMS:
        _legacy_cache.popitem(last=False)


async def render_page(message_id: int, secure_hash: str) -> str:
    key = (int(message_id), str(secure_hash))
    cached = _legacy_cache_get(key)
    if cached is not None:
        file_name, mime_type, size_bytes = cached
        quoted_filename = quote_media_name(file_name)
        src = f"{Var.URL.rstrip('/')}/{secure_hash}{message_id}/{quoted_filename}"
        return await render_media_page(
            file_name, src, mime_type=mime_type, size_bytes=size_bytes or None
        )

    try:
        from Thunder.bot import StreamBot  # layering break: lazy import
        from Thunder.server.exceptions import InvalidHash, TelegramUnavailable

        # every miss costs an RPC, so shed load here rather than queueing it
        with _preview_rpc_slot():
            try:
                message = await tg_call(
                    StreamBot.get_messages,
                    chat_id=int(Var.BIN_CHANNEL),
                    message_ids=int(message_id),
                    retries=1,
                    timeout=60,
                )
            except TimeoutError as e:
                # vault timeout is transient, never absence: map to 503 (not
                # FileNotFound self-heal, not ladder 500) like the delivery path
                raise TelegramUnavailable(f"vault lookup timed out: {e}") from e

        if not message:
            raise InvalidHash("Message not found")
        if isinstance(message, list):  # defensive: pyrogram returns a list for list inputs
            if not message:
                raise InvalidHash("Message not found")
            message = message[0]

        file_unique_id = get_uniqid(message)
        file_name = get_fname(message)

        if not file_unique_id or file_unique_id[:6] != secure_hash:
            raise InvalidHash("File unique ID or secure hash mismatch during rendering.")

        media = get_media(message)
        mime_type = getattr(media, "mime_type", None) or None
        if not mime_type and media is not None:
            mime_type = ext_and_mime_for_class(type(media).__name__.lower())[1]

        _legacy_cache_put(key, file_name, mime_type, file_size := get_fsize(message))

        quoted_filename = quote_media_name(file_name)
        src = f"{Var.URL.rstrip('/')}/{secure_hash}{message_id}/{quoted_filename}"
        return await render_media_page(
            file_name, src, mime_type=mime_type, size_bytes=file_size or None
        )
    except InvalidHash as e:
        # a 404 is the expected answer for a wrong capability hash, and the
        # route ladder logs it at debug. Logging it as an ERROR with a
        # traceback let an unauthenticated caller fill bot.txt with one
        # traceback per probe -- the same amplifier, aimed at the log file.
        logger.debug(f"render_page: {type(e).__name__} for message_id {message_id}")
        raise
    except web.HTTPException:
        # load shed, not a fault: the caller already gets the right status and
        # logging a traceback for every rejected request would be its own outage
        raise
    except Exception as e:
        # the capability hash is a credential: never log it (bot.txt is uploaded via /log)
        logger.error(
            f"Error in render_page for message_id {message_id} (hash redacted): {e}",
            exc_info=True,
        )
        raise
