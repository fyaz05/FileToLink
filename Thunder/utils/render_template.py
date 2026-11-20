"""Renders the HTML player page from a stored file record."""

import asyncio
import hashlib
import urllib.parse
from pathlib import Path

import minijinja
from pyrogram.errors import FloodWait

from Thunder.bot import StreamBot
from Thunder.server.exceptions import InvalidHash
from Thunder.utils.bot_utils import quote_media_name
from Thunder.utils.file_properties import get_fname, get_fsize, get_mime_type, get_uniqid
from Thunder.utils.human_readable import humanbytes
from Thunder.utils.logger import logger
from Thunder.vars import Var

SITE_NAME = "Thunder"
REPO_URL = "https://github.com/fyaz05/FileToLink"
LOGO_URL = "https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/ThunderGo/logo.png"

# Module-relative: the bot may start from any working directory.
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "template"


def _asset_version() -> str:
    """Content hash of the player assets, for cache-busting query strings."""
    digest = hashlib.sha256()
    for name in ("player.css", "player.js"):
        try:
            digest.update((TEMPLATE_DIR / name).read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


ASSET_VERSION = _asset_version()

# movi-player demuxes the container itself, so these decide only which surface
# to show, never whether playback works.
_VIDEO_EXTENSIONS = frozenset({
    "3g2", "3gp", "asf", "avi", "divx", "f4v", "flv", "m2ts", "m2v", "m4v",
    "mkv", "mov", "mp4", "mpeg", "mpg", "mts", "mxf", "ogv", "rm", "rmvb",
    "ts", "vob", "webm", "wmv",
})
_AUDIO_EXTENSIONS = frozenset({
    "aac", "ac3", "aif", "aiff", "alac", "ape", "dts", "flac", "m4a", "mka",
    "mp3", "ogg", "opus", "wav", "wma",
})
_IMAGE_EXTENSIONS = frozenset({
    "avif", "bmp", "gif", "heic", "heif", "ico", "jpeg", "jpg", "png", "svg",
    "tif", "tiff", "webp",
})

_MEDIA_KINDS = (
    ("video", _VIDEO_EXTENSIONS),
    ("audio", _AUDIO_EXTENSIONS),
    ("image", _IMAGE_EXTENSIONS),
)


def _extension_of(file_name: str) -> str:
    _, dot, ext = (file_name or "").rpartition(".")
    if not dot or "/" in ext or "\\" in ext:
        return ""
    return ext.strip().lower()


def classify_media(file_name: str, mime_type: str) -> str:
    """Classify a file as ``video``, ``audio``, ``image`` or ``other``.

    The extension wins: Telegram labels even an MKV document upload
    ``application/octet-stream``.
    """
    extension = _extension_of(file_name)
    for kind, extensions in _MEDIA_KINDS:
        if extension in extensions:
            return kind

    prefix = (mime_type or "").strip().lower().split("/", 1)[0]
    return prefix if prefix in {"video", "audio", "image"} else "other"


# Unknown variables must raise: no contextual autoescaping to fall back on.
template_env = minijinja.Environment(
    loader=minijinja.load_from_path([str(TEMPLATE_DIR)]),
    undefined_behavior="strict",
    keep_trailing_newline=True,
)


async def render_media_page(
    file_name: str,
    stream_url: str,
    *,
    mime_type: str = "application/octet-stream",
    file_size: int = 0,
) -> str:
    """Render the player page for one file.

    ``stream_url`` must be percent-encoded with ``?disposition=inline``; the
    template escapes it (``|tojson`` in config, autoescape in markup).
    """
    normalized = (mime_type or "application/octet-stream").strip().lower()
    download_url = stream_url.split("?", 1)[0]
    kind = classify_media(file_name, normalized)

    return template_env.render_template(
        "req.html",
        file_name=file_name,
        stream_url=stream_url,
        download_url=download_url,
        mime_type=normalized,
        size_formatted=humanbytes(file_size) if file_size else "Unknown",
        is_video=kind == "video",
        is_audio=kind == "audio",
        is_image=kind == "image",
        site_name=SITE_NAME,
        repo_url=REPO_URL,
        logo_url=LOGO_URL,
        asset_version=ASSET_VERSION,
    )


async def render_page(message_id: int, secure_hash: str) -> str:
    try:
        try:
            message = await StreamBot.get_messages(chat_id=int(Var.BIN_CHANNEL), message_ids=message_id)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            message = await StreamBot.get_messages(chat_id=int(Var.BIN_CHANNEL), message_ids=message_id)

        if not message:
            raise InvalidHash("Message not found")

        file_unique_id = get_uniqid(message)
        file_name = get_fname(message)

        if not file_unique_id or file_unique_id[:6] != secure_hash:
            raise InvalidHash("File unique ID or secure hash mismatch during rendering.")

        quoted_filename = quote_media_name(file_name)
        src = urllib.parse.urljoin(Var.URL, f'{secure_hash}{message_id}/{quoted_filename}')
        return await render_media_page(
            file_name,
            f"{src}?disposition=inline",
            mime_type=get_mime_type(message),
            file_size=get_fsize(message),
        )
    except Exception as e:
        logger.error(
            f"Error in render_page for message_id {message_id} and hash {secure_hash}: {e}",
            exc_info=True
        )
        raise
