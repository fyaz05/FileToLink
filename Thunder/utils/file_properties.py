from pyrogram import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId
from Thunder.server.exceptions import FileNotFound
from typing import Any, Optional
from datetime import datetime

def get_media(msg: Message) -> Optional[Any]:
    m_attrs = (
        "audio", "document", "photo", "sticker",
        "animation", "video", "voice", "video_note"
    )
    for attr in m_attrs:
        media = getattr(msg, attr, None)
        if media:
            return media
    return None

async def get_fids(cli: Client, chat_id: int, message_id: int) -> Optional[FileId]:
    try:
        msg_obj = await cli.get_messages(chat_id, message_id)
    except Exception:
        raise FileNotFound("Error fetching message")

    if not msg_obj or msg_obj.empty:
        raise FileNotFound("Message not found or empty")

    media = get_media(msg_obj)
    if not media:
        raise FileNotFound("No media in message")

    if not hasattr(media, 'file_id') or not hasattr(media, 'file_unique_id'):
        raise FileNotFound("Media metadata incomplete")

    try:
        fid_obj = FileId.decode(media.file_id)
        fid_obj.file_size = getattr(media, "file_size", 0)
        fid_obj.mime_type = getattr(media, "mime_type", "")
        fid_obj.file_name = get_fname(msg_obj)
    except Exception as e:
        raise FileNotFound(f"Error processing file_id: {str(e)}")

    return fid_obj

def parse_fid(msg: Message) -> Optional[FileId]:
    media = get_media(msg)
    if media and hasattr(media, 'file_id'):
        try:
            return FileId.decode(media.file_id)
        except Exception:
            return None
    return None

def get_uniqid(msg: Message) -> Optional[str]:
    media = get_media(msg)
    if media and hasattr(media, 'file_unique_id'):
        return media.file_unique_id
    return None

def get_hash(msg: Message) -> str:
    media = get_media(msg)
    if media and hasattr(media, 'file_unique_id') and media.file_unique_id:
        return media.file_unique_id[:6]
    return ''

def get_fname(msg: Message) -> str:
    media = get_media(msg)
    fname = ""

    if media and hasattr(media, 'file_name'):
        fname = media.file_name

    if not fname:
        mtype = "unknown"
        if msg.media:
            mtype = msg.media.value

        formats = {
            "photo": "jpg", "audio": "mp3", "voice": "ogg",
            "video": "mp4", "animation": "mp4", "video_note": "mp4",
            "document": "zip", "sticker": "webp",
        }

        ext = formats.get(mtype, "")

        if media and hasattr(media, 'mime_type') and media.mime_type:
            mime_parts = media.mime_type.split('/')
            if len(mime_parts) == 2:
                potential_ext = mime_parts[1]
                if potential_ext == "mpeg": potential_ext = "mp3"
                elif potential_ext == "jpeg": potential_ext = "jpg"
                elif potential_ext in ["ogg", "mp4", "webp", "zip"]: pass
                else:
                    if len(potential_ext) <= 4 and potential_ext.isalnum():
                         ext = potential_ext

        fext = "." + ext if ext else ""
        dt_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fname = f"{mtype}-{dt_str}{fext}"

    return fname if fname else "unknown-file"

def get_fsize(msg: Message) -> int:
    media = get_media(msg)
    if media and hasattr(media, 'file_size'):
        return media.file_size
    return 0
