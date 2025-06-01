# Thunder/utils/file_properties.py

from pyrogram import Client
from pyrogram.types import Message  
from pyrogram.file_id import FileId
from Thunder.server.exceptions import FileNotFound
from Thunder.utils.error_handling import log_errors
from typing import Any, Optional

def get_media_from_message(message: Message) -> Optional[Any]:
    for attr in ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note"):
        if media := getattr(message, attr, None):
            return media
    return None

@log_errors
async def get_file_ids(client: Client, chat_id: int, message_id: int) -> Optional[FileId]: # Return Optional
    message = await client.get_messages(chat_id, message_id)
    if not message or message.empty:
        raise FileNotFound("Message not found/empty")
    
    media = get_media_from_message(message)
    if not media:
        raise FileNotFound("No media in message")
    
    if not hasattr(media, 'file_id') or not hasattr(media, 'file_unique_id'):
        raise FileNotFound("Media metadata incomplete")

    file_id_obj = FileId.decode(media.file_id)
    file_id_obj.file_size = getattr(media, "file_size", 0)
    file_id_obj.mime_type = getattr(media, "mime_type", "")
    file_id_obj.file_name = getattr(media, "file_name", "")
    file_id_obj.unique_id = media.file_unique_id
    return file_id_obj

@log_errors
def parse_file_id(message: Message) -> Optional[FileId]:
    media = get_media_from_message(message)
    return FileId.decode(media.file_id) if media and hasattr(media, 'file_id') else None

@log_errors
def parse_file_unique_id(message: Message) -> Optional[str]:
    media = get_media_from_message(message)
    return media.file_unique_id if media and hasattr(media, 'file_unique_id') else None

@log_errors
def get_hash(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return media.file_unique_id[:6] if media and hasattr(media, 'file_unique_id') and media.file_unique_id else ''

@log_errors
def get_name(media_msg: Message) -> str:
    media = get_media_from_message(media_msg)
    return getattr(media, 'file_name', '') if media and hasattr(media, 'file_name') else ''

@log_errors
def get_media_file_size(message: Message) -> int:
    media = get_media_from_message(message)
    return getattr(media, 'file_size', 0) if media and hasattr(media, 'file_size') else 0
