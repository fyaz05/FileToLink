# Thunder/utils/file_properties.py

import time
from pyrogram.client import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId
from Thunder.server.exceptions import FileNotFound
from typing import Any, Optional
from datetime import datetime as dt
from Thunder.utils.logger import logger

def get_media(message: Message) -> Optional[Any]:
    """
    Returns main media from a message and logs thumbnail availability
    """
    for attr in ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note"):
        media = getattr(message, attr, None)
        if media:
            # Log if thumbnail is available
            if hasattr(media, 'thumbs') and media.thumbs:
                pass # Removed debug log
            return media
    return None

async def get_fids(client: Client, chat_id: int, message_id: int) -> FileId:
    try:
        msg = await client.get_messages(chat_id, message_id)
        if not msg or getattr(msg, 'empty', False):
            raise FileNotFound("Message not found")
        
        media = get_media(msg)
        if media:
            if not hasattr(media, 'file_id') or not hasattr(media, 'file_unique_id'):
                raise FileNotFound("Media metadata incomplete")
            
            return FileId.decode(media.file_id)
        
        # No media found
        raise FileNotFound("No media in message")
        
    except Exception as e:
        logger.error(f"Error in get_fids: {e}")
        raise FileNotFound(str(e))

def parse_fid(message: Message) -> Optional[FileId]:
    media = get_media(message)
    if media and hasattr(media, 'file_id'):
        try:
            return FileId.decode(media.file_id)
        except Exception:
            return None
    return None

def get_uniqid(message: Message) -> Optional[str]:
    media = get_media(message)
    return getattr(media, 'file_unique_id', None)

def get_hash(media_msg: Message) -> str:
    uniq_id = get_uniqid(media_msg)
    return uniq_id[:6] if uniq_id else ''

def get_fname(msg: Message) -> str:
    start_time = time.time()
    
    media = get_media(msg)
    fname = None
    if media:
        fname = getattr(media, 'file_name', None)

    if not fname:
        media_type_str = "unknown_media"
        if msg.media:
            # Ensure media_type_str is always a string
            media_type_value = msg.media.value
            if media_type_value:
                media_type_str = str(media_type_value)

        # Use Pyrogram's file type to determine extension
        ext = ""
        if media and hasattr(media, '_file_type'):
            file_type = media._file_type
            if file_type == "photo":
                ext = "jpg"
            elif file_type == "audio":
                ext = "mp3"
            elif file_type == "voice":
                ext = "ogg"
            elif file_type in ["video", "animation", "video_note"]:
                ext = "mp4"
            elif file_type == "sticker":
                ext = "webp"
            else:
                ext = "bin"

        timestamp = dt.now().strftime("%Y%m%d%H%M%S")
        fname = f"Thunder File To Link_{timestamp}.{ext}"

    latency = time.time() - start_time
    return fname

def get_fsize(message: Message) -> int:
    media = get_media(message)
    return getattr(media, 'file_size', 0) if media else 0
