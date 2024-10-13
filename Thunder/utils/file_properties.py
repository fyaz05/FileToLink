from pyrogram import Client
from typing import Any, Optional
from pyrogram.types import Message
from pyrogram.file_id import FileId
from pyrogram.raw.types.messages import Messages
from Thunder.server.exceptions import FileNotFound
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def parse_file_id(message: Message) -> Optional[FileId]:
    """Extracts and decodes the file ID from a message."""
    media = get_media_from_message(message)
    if media:
        return FileId.decode(media.file_id)
    logging.warning(f"No media found in message: {message.message_id}")
    return None

async def parse_file_unique_id(message: Messages) -> Optional[str]:
    """Extracts the unique file ID if available."""
    media = get_media_from_message(message)
    if media:
        return media.file_unique_id
    logging.warning(f"No media found in message for unique ID extraction.")
    return None

async def get_file_ids(client: Client, chat_id: int, message_id: int) -> Optional[FileId]:
    """Fetches and parses file IDs from a message."""
    try:
        message = await client.get_messages(chat_id, message_id)
        if message.empty:
            logging.error("Message is empty; file not found.")
            raise FileNotFound("Message not found or is empty.")

        media = get_media_from_message(message)
        if not media:
            logging.error("No media in message; cannot fetch file IDs.")
            raise FileNotFound("No media in message.")

        file_unique_id = await parse_file_unique_id(message)
        file_id = await parse_file_id(message)

        # Add extra details to FileId
        if file_id:  # Ensure file_id is not None
            file_id.file_size = getattr(media, "file_size", 0)
            file_id.mime_type = getattr(media, "mime_type", "")
            file_id.file_name = getattr(media, "file_name", "")
            file_id.unique_id = file_unique_id
        
        return file_id

    except Exception as e:
        logging.error(f"An error occurred while getting file IDs: {e}")
        return None

def get_media_from_message(message: Message) -> Any:
    """Checks the message for different types of media content."""
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )

    for attr in media_types:
        media = getattr(message, attr, None)
        if media:
            logging.info(f"Media found in message: {attr}")
            return media
    logging.info("No media types found in the message.")
    return None

def get_hash(media_msg: Message) -> str:
    """Generates a hash from the unique file ID of the media."""
    media = get_media_from_message(media_msg)
    return getattr(media, "file_unique_id", "")[:6]

def get_name(media_msg: Message) -> str:
    """Retrieves the file name from the media if available."""
    media = get_media_from_message(media_msg)
    return getattr(media, "file_name", "")

def get_media_file_size(message: Message) -> int:
    """Returns the file size of the media content."""
    media = get_media_from_message(message)
    return getattr(media, "file_size", 0)
