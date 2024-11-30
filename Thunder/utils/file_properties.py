# Thunder/utils/file_properties.py

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.file_id import FileId
from Thunder.server.exceptions import FileNotFound
from Thunder.utils.logger import logger
from typing import Any, Optional


def get_media_from_message(message: Message) -> Optional[Any]:
    """
    Extracts the media object from a message.

    Args:
        message (Message): The message object.

    Returns:
        Optional[Any]: The media object if found, else None.
    """
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
            logger.debug(f"Media found in message: {attr}")
            return media
    logger.debug("No media types found in the message.")
    return None


def parse_file_id(message: Message) -> Optional[FileId]:
    """
    Extracts and decodes the file ID from a message.

    Args:
        message (Message): The message containing the media.

    Returns:
        Optional[FileId]: The decoded FileId object if successful, else None.
    """
    media = get_media_from_message(message)
    if media:
        return FileId.decode(media.file_id)
    logger.warning(f"No media found in message: {message.message_id}")
    return None


def parse_file_unique_id(message: Message) -> Optional[str]:
    """
    Extracts the unique file ID if available.

    Args:
        message (Message): The message containing the media.

    Returns:
        Optional[str]: The unique file ID if found, else None.
    """
    media = get_media_from_message(message)
    if media:
        return media.file_unique_id
    logger.warning("No media found in message for unique ID extraction.")
    return None


async def get_file_ids(client: Client, chat_id: int, message_id: int) -> FileId:
    """
    Fetches and parses file IDs from a message.

    Args:
        client (Client): The Pyrogram client instance.
        chat_id (int): The chat ID containing the message.
        message_id (int): The message ID.

    Returns:
        FileId: The FileId object with additional properties.

    Raises:
        FileNotFound: If the message or media is not found.
    """
    try:
        message = await client.get_messages(chat_id, message_id)
        if not message or message.empty:
            logger.error("Message is empty or not found.")
            raise FileNotFound("Message not found or is empty.")

        media = get_media_from_message(message)
        if not media:
            logger.error("No media in message; cannot fetch file IDs.")
            raise FileNotFound("No media in message.")

        file_unique_id = parse_file_unique_id(message)
        file_id = parse_file_id(message)

        # Add extra details to FileId
        if file_id:
            file_id.file_size = getattr(media, "file_size", 0)
            file_id.mime_type = getattr(media, "mime_type", "")
            file_id.file_name = getattr(media, "file_name", "")
            file_id.unique_id = file_unique_id

        return file_id

    except Exception as e:
        logger.error(f"An error occurred while getting file IDs: {e}", exc_info=True)
        raise


def get_hash(media_msg: Message) -> str:
    """
    Generates a hash from the unique file ID of the media.

    Args:
        media_msg (Message): The message containing the media.

    Returns:
        str: The first 6 characters of the file's unique ID.
    """
    media = get_media_from_message(media_msg)
    return getattr(media, "file_unique_id", "")[:6]


def get_name(media_msg: Message) -> str:
    """
    Retrieves the file name from the media if available.

    Args:
        media_msg (Message): The message containing the media.

    Returns:
        str: The file name, or an empty string if not available.
    """
    media = get_media_from_message(media_msg)
    return getattr(media, "file_name", "")


def get_media_file_size(message: Message) -> int:
    """
    Returns the file size of the media content.

    Args:
        message (Message): The message containing the media.

    Returns:
        int: The file size in bytes.
    """
    media = get_media_from_message(message)
    return getattr(media, "file_size", 0)
