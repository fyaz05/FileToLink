# Thunder/utils/file_properties.py

from datetime import datetime as dt
from typing import Any

from pyrogram.file_id import FileId
from pyrogram.types import Message

from Thunder.utils.media_types import canonical_media_type, ext_for


def get_media(message: Message) -> Any | None:
    for attr in (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    ):
        media = getattr(message, attr, None)
        if media:
            return media
    return None


def get_uniqid(message: Message) -> str | None:
    media = get_media(message)
    return getattr(media, "file_unique_id", None)


def get_hash(media_msg: Message) -> str:
    uniq_id = get_uniqid(media_msg)
    return uniq_id[:6] if uniq_id else ""


def get_fsize(message: Message) -> int:
    media = get_media(message)
    return getattr(media, "file_size", 0) if media else 0


def parse_fid(message: Message) -> FileId | None:
    media = get_media(message)
    if media and hasattr(media, "file_id"):
        try:
            return FileId.decode(media.file_id)
        except Exception:
            return None
    return None


def get_fname(msg: Message) -> str:
    media = get_media(msg)
    fname = getattr(media, "file_name", None) if media else None

    if not fname:
        ext = "bin"
        if media:
            # single media-type map (H4c): attribute -> canonical key -> ext
            for attr in ("photo", "audio", "voice", "video", "animation", "video_note", "sticker"):
                if getattr(msg, attr, None) is not None:
                    ext = ext_for(canonical_media_type(attr=attr))
                    break

        timestamp = dt.now().strftime("%Y%m%d%H%M%S")
        fname = f"Thunder File To Link_{timestamp}.{ext}"

    return fname
