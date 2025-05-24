# Thunder/utils/file_properties.py

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId
from Thunder.server.exceptions import FileNotFound
from Thunder.utils.logger import logger
from typing import Any, Optional

def get_media_from_message(message: Message) -> Optional[Any]:
    # Extract media from message using known media type attributes
    media_types = ("audio", "document", "photo", "sticker", 
                  "animation", "video", "voice", "video_note")
    
    for attr in media_types:
        if media := getattr(message, attr, None):
            return media
    return None

def parse_file_id(message: Message) -> Optional[FileId]:
    # Decode FileId from media if available
    if media := get_media_from_message(message):
        return FileId.decode(media.file_id)
    logger.warning(f"No media found in message: {message.id}")
    return None

def parse_file_unique_id(message: Message) -> Optional[str]:
    # Extract unique file ID from media if available
    if media := get_media_from_message(message):
        return media.file_unique_id
    logger.warning("No media found for unique ID extraction")
    return None

async def get_file_ids(client: Client, chat_id: int, message_id: int) -> FileId:
    # Fetch and process media file details from message
    try:
        message = await client.get_messages(chat_id, message_id)
        if not message or message.empty:
            logger.debug("Message not found or empty")
            raise FileNotFound("Message not found/empty")

        if not (media := get_media_from_message(message)):
            logger.debug("Message contains no media")
            raise FileNotFound("No media in message")

        file_id = parse_file_id(message)
        if not file_id:
            logger.debug("Failed to parse file ID")
            raise FileNotFound("Invalid file ID")

        # Enhance FileId object with additional metadata
        file_id.file_size = getattr(media, "file_size", 0)
        file_id.mime_type = getattr(media, "mime_type", "")
        file_id.file_name = getattr(media, "file_name", "")
        file_id.unique_id = parse_file_unique_id(message)

        return file_id

    except Exception as e:
        logger.debug(f"File ID processing failed: {str(e)}")
        raise

def get_hash(media_msg: Message) -> str:
    # Generate short hash from file unique ID
    if media := get_media_from_message(media_msg):
        return media.file_unique_id[:6]
    return ""

def get_name(media_msg: Message) -> str:
    # Extract filename from media if available
    return getattr(get_media_from_message(media_msg), "file_name", "")

def get_media_file_size(message: Message) -> int:
    # Get file size from media content
    return getattr(get_media_from_message(message), "file_size", 0)
