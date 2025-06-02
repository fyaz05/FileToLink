# Thunder/utils/custom_dl.py

from typing import Dict, Any, AsyncGenerator
from pyrogram import Client
from pyrogram.types import Message
from Thunder.vars import Var
from Thunder.server.exceptions import FileNotFound
from Thunder.utils.logger import logger

class ByteStreamer:
    __slots__ = ('client', 'chat_id')
    
    def __init__(self, client: Client):
        """
        Initializes the ByteStreamer with a Pyrogram client and sets the target chat ID.
        """
        self.client = client
        self.chat_id = int(Var.BIN_CHANNEL)

    async def get_message(self, message_id: int) -> Message:
        """
        Fetches a message with media by its ID from the configured chat.
        
        Raises:
            FileNotFound: If the message does not exist or contains no media.
        
        Returns:
            The Pyrogram Message object containing media.
        """
        try:
            message = await self.client.get_messages(self.chat_id, message_id)
            if not message or not message.media:
                raise FileNotFound(f"Message {message_id} not found")
            return message
        except Exception as e:
            logger.debug(f"Error fetching message {message_id}: {e}")
            raise FileNotFound(f"Message {message_id} not found")

    async def stream_file(self, message_id: int, offset: int = 0, limit: int = 0) -> AsyncGenerator[bytes, None]:
        """
        Asynchronously streams the media content of a message in chunks.
        
        If a positive limit is specified, streams only the specified portion of the media starting from the given offset; otherwise, streams the entire media file.
        
        Args:
            message_id: The ID of the message containing the media to stream.
            offset: The starting byte offset for streaming. Defaults to 0.
            limit: The maximum number of bytes to stream. If 0, streams the entire file.
        
        Yields:
            Chunks of the media file as bytes.
        """
        message = await self.get_message(message_id)
        
        if limit > 0:
            chunk_offset = offset // (1024 * 1024)
            chunk_limit = (limit + 1024 * 1024 - 1) // (1024 * 1024)
            
            async for chunk in self.client.stream_media(message, offset=chunk_offset, limit=chunk_limit):
                yield chunk
        else:
            async for chunk in self.client.stream_media(message):
                yield chunk

    def get_file_info_sync(self, message: Message) -> Dict[str, Any]:
        """
        Extracts metadata from the media content of a Telegram message.
        
        If the message contains a document, video, audio, or photo, returns a dictionary with file size, name, MIME type, unique ID, and media type. If no media is present, returns a dictionary with an error message.
        
        Args:
            message: The Telegram message object to extract media information from.
        
        Returns:
            A dictionary containing media metadata or an error message if no media is found.
        """
        media = message.document or message.video or message.audio or message.photo
        if not media:
            return {"message_id": message.id, "error": "No media"}
        
        return {
            "message_id": message.id,
            "file_size": getattr(media, 'file_size', 0) or 0,
            "file_name": getattr(media, 'file_name', None),
            "mime_type": getattr(media, 'mime_type', None),
            "unique_id": getattr(media, 'file_unique_id', None),
            "media_type": type(media).__name__.lower()
        }

    async def get_file_info(self, message_id: int) -> Dict[str, Any]:
        """
        Asynchronously retrieves metadata for the media file in a specified message.
        
        Fetches the message by its ID and extracts media information such as file size, name, MIME type, and media type. Returns an error dictionary if the message cannot be retrieved or does not contain media.
        
        Args:
            message_id: The ID of the message containing the media.
        
        Returns:
            A dictionary with file metadata or an error message if retrieval fails.
        """
        try:
            message = await self.get_message(message_id)
            return self.get_file_info_sync(message)
        except Exception as e:
            logger.debug(f"Error getting file info for {message_id}: {e}")
            return {"message_id": message_id, "error": str(e)}
