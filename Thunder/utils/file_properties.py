from datetime import datetime as dt
from typing import Any

import pytdbot
from pytdbot import types

from Thunder.server.exceptions import FileNotFound
from Thunder.utils.compat import (
    _get_file_name,
    _get_file_size,
    _get_file_unique_id,
    _get_media_file,
)
from Thunder.utils.logger import logger
from Thunder.utils.media_helpers import _get_extension_for_content_type


def get_media(message: types.Message) -> Any | None:
    content = getattr(message, "content", None)
    if content is None:
        return None
    media_map = {
        types.MessageAudio: lambda c: c.audio,
        types.MessageDocument: lambda c: c.document,
        types.MessagePhoto: lambda c: c.photo,
        types.MessageSticker: lambda c: c.sticker,
        types.MessageAnimation: lambda c: c.animation,
        types.MessageVideo: lambda c: c.video,
        types.MessageVoiceNote: lambda c: c.voice,
        types.MessageVideoNote: lambda c: c.video,
    }
    for media_type, getter in media_map.items():
        if isinstance(content, media_type):
            return getter(content)
    return None


def get_uniqid(message: types.Message) -> str | None:
    return _get_file_unique_id(message)


def get_hash(media_msg: types.Message) -> str:
    uniq_id = get_uniqid(media_msg)
    return uniq_id[:6] if uniq_id else ''


def get_fsize(message: types.Message) -> int:
    return _get_file_size(message)


def parse_fid(message: types.Message) -> Any | None:
    media_file = _get_media_file(message)
    if media_file:
        return media_file
    return None


def get_fname(msg: types.Message) -> str:
    fname = _get_file_name(msg)
    if not fname:
        content = getattr(msg, "content", None)
        ext = ".bin"
        if content:
            content_type = type(content).__name__.lower()
            media_type = content_type.replace("message", "", 1) if content_type.startswith("message") else content_type
            ext = _get_extension_for_content_type(media_type)
        timestamp = dt.now().strftime("%Y%m%d%H%M%S")
        fname = f"Thunder File To Link_{timestamp}{ext}"
    return fname


async def get_fids(client: pytdbot.Client, chat_id: int, message_id: int):
    try:
        result = await client.getMessage(chat_id=chat_id, message_id=message_id)
        if isinstance(result, types.Error):
            raise FileNotFound(f"Message not found: {result.message}")
        if not result or getattr(result, "empty", False):
            raise FileNotFound("Message not found")

        media_file = _get_media_file(result)
        if not media_file:
            raise FileNotFound("No media in message")
        return media_file
    except Exception as e:
        logger.error(f"Error in get_fids: {e}", exc_info=True)
        raise FileNotFound(str(e))
