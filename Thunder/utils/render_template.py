# Thunder/utils/render_template.py

import time
import urllib.parse
from collections import OrderedDict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.file_properties import get_fname, get_uniqid
from Thunder.utils.logger import logger
from Thunder.utils.safe_call import tg_call
from Thunder.vars import Var

# NOTE: lazy import of Thunder.server.exceptions inside render_page() avoids
# a circular import (server/__init__ -> stream_routes -> here).

# M2: resolve templates relative to the package, not the process CWD.
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
    """M2: typed player page -- derive the layout from the mime type.

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
) -> str:
    # NOTE: src must be a pre-encoded URL. Templates use |safe to avoid double-encoding.
    template = template_env.get_template("req.html")
    context = {
        "heading": f"View {file_name}",
        "file_name": file_name,
        "src": f"{src}?disposition=inline",
        "kind": _page_kind(mime_type, file_name),
        "mime_type": mime_type or "application/octet-stream",
    }
    return await template.render_async(**context)


# L1: TTL+LRU cache so repeat legacy /watch views don't re-fetch the vault message.
_legacy_cache: "OrderedDict[tuple[int, str], tuple[float, str]]" = OrderedDict()
_LEGACY_CACHE_TTL_SECONDS = 600
_LEGACY_CACHE_MAX_ITEMS = 1024


def _legacy_cache_get(key) -> str | None:
    cached = _legacy_cache.get(key)
    if not cached:
        return None
    ts, file_name = cached
    if time.monotonic() - ts > _LEGACY_CACHE_TTL_SECONDS:
        _legacy_cache.pop(key, None)
        return None
    _legacy_cache.move_to_end(key)
    return file_name


def _legacy_cache_put(key, file_name: str) -> None:
    _legacy_cache[key] = (time.monotonic(), file_name)
    _legacy_cache.move_to_end(key)
    while len(_legacy_cache) > _LEGACY_CACHE_MAX_ITEMS:
        _legacy_cache.popitem(last=False)


async def render_page(message_id: int, secure_hash: str) -> str:
    key = (int(message_id), str(secure_hash))
    cached = _legacy_cache_get(key)
    if cached is not None:
        file_name = cached
        quoted_filename = quote_media_name(file_name)
        src = urllib.parse.urljoin(Var.URL, f"{secure_hash}{message_id}/{quoted_filename}")
        return await render_media_page(file_name, src)

    try:
        from Thunder.bot import StreamBot  # M12 layering break: lazy import
        from Thunder.server.exceptions import InvalidHash

        message = await tg_call(
            StreamBot.get_messages,
            chat_id=int(Var.BIN_CHANNEL),
            message_ids=int(message_id),
            retries=1,
            timeout=60,
        )

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

        _legacy_cache_put(key, file_name)

        quoted_filename = quote_media_name(file_name)
        src = urllib.parse.urljoin(Var.URL, f"{secure_hash}{message_id}/{quoted_filename}")
        return await render_media_page(file_name, src)
    except Exception as e:
        # the capability hash is a credential: never log it (bot.txt is uploaded via /log)
        logger.error(
            f"Error in render_page for message_id {message_id} (hash redacted): {e}",
            exc_info=True,
        )
        raise
