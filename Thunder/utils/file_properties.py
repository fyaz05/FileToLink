from pyrogram import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId
from Thunder.server.exceptions import FileNotFound
from typing import Any, Optional
from datetime import datetime as dt

def get_media(message: Message) -> Optional[Any]:
    for attr in ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note"):
        media = getattr(message, attr, None)
        if media:
            return media
    return None

async def get_fids(client: Client, chat_id: int, message_id: int) -> Optional[FileId]:
    try:
        msg_obj = await client.get_messages(chat_id, message_id)
    except Exception as e:
        raise FileNotFound(f"Error fetching message for get_fids: {e}")

    if not msg_obj or getattr(msg_obj, 'empty', False):
        raise FileNotFound("Message not found or empty in get_fids")

    media = get_media(msg_obj)
    if not media:
        raise FileNotFound("No media in message for get_fids")

    if not hasattr(media, 'file_id') or not hasattr(media, 'file_unique_id'):
        raise FileNotFound("Media metadata incomplete in get_fids (missing file_id or file_unique_id)")

    try:
        file_id_obj = FileId.decode(media.file_id)
    except Exception as e:
        raise FileNotFound(f"Error decoding file_id in get_fids: {e}")

    file_id_obj.file_size = getattr(media, "file_size", 0)
    file_id_obj.mime_type = getattr(media, "mime_type", "")
    file_id_obj.file_name = getattr(media, "file_name", "")

    if not hasattr(file_id_obj, 'unique_id') or not file_id_obj.unique_id:
         file_id_obj.unique_id = media.file_unique_id

    return file_id_obj

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
    media = get_media(msg)
    fname = None
    if media:
        fname = getattr(media, 'file_name', None)

    if not fname:
        media_type_str = "unknown_media"
        if msg.media:
            media_type_str = msg.media.value if msg.media.value else "unknown_media"

        ext = ""
        if media and getattr(media, 'mime_type', None):
            mime_type = media.mime_type
            if "/" in mime_type:
                potential_ext = mime_type.split('/')[-1]
                if potential_ext == "jpeg": potential_ext = "jpg"
                elif potential_ext == "mpeg": potential_ext = "mp3"
                if potential_ext.isalnum() and len(potential_ext) <= 5:
                    ext = potential_ext

        if not ext:
            formats = {
                "photo": "jpg", "audio": "mp3", "voice": "ogg",
                "video": "mp4", "animation": "mp4", "video_note": "mp4",
                "document": "bin", "sticker": "webp",
            }
            ext = formats.get(media_type_str, "bin")

        unique_part = get_uniqid(msg)
        if unique_part:
            unique_part = unique_part[:8]
        else:
            unique_part = dt.now().strftime("%Y%m%d%H%M%S")

        fname = f"{media_type_str}_{unique_part}.{ext}"

    return fname if fname else "unknown_file.bin"

def get_fsize(message: Message) -> int:
    media = get_media(message)
    return getattr(media, 'file_size', 0) if media else 0
