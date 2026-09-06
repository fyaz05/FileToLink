# Thunder/utils/custom_dl.py

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from Thunder.server.exceptions import FileNotFound
from Thunder.utils.file_properties import get_media
from Thunder.utils.logger import logger
from Thunder.utils.media_types import ext_and_mime_for_class
from Thunder.vars import Var


class ByteStreamer:
    __slots__ = ("client", "chat_id")

    def __init__(self, client: Client) -> None:
        self.client = client
        self.chat_id = int(Var.BIN_CHANNEL)

    async def get_message(self, message_id: int) -> Message:
        while True:
            try:
                message = await self.client.get_messages(self.chat_id, message_id)
                break
            except FloodWait as e:
                logger.debug(f"FloodWait: get_message, sleep {e.value}s")
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.debug(f"Error fetching message {message_id}: {e}", exc_info=True)
                raise FileNotFound(f"Message {message_id} not found") from e

        if not message or not message.media:
            raise FileNotFound(f"Message {message_id} not found")
        return message

    async def stream_file(
        self,
        media_ref: int | Message,
        offset: int = 0,
        limit: int = 0,
    ) -> AsyncGenerator[bytes]:
        chunk_offset = offset // (1024 * 1024)
        chunk_limit = 0
        if limit > 0:
            chunk_limit = ((limit + (1024 * 1024) - 1) // (1024 * 1024)) + 1

        # H4b: the historical fallback-message plumbing was dead (the
        # fallback id always equalled the primary ref, so the fallback ref
        # was never appended) -- removed.
        while True:
            try:
                target = (
                    await self.get_message(media_ref) if isinstance(media_ref, int) else media_ref
                )
                async for chunk in self.client.stream_media(
                    target, offset=chunk_offset, limit=chunk_limit
                ):
                    yield chunk
                return
            except FloodWait as e:
                logger.debug(f"FloodWait: stream_file, sleep {e.value}s")
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.debug(f"Error streaming media ref {media_ref}: {e}", exc_info=True)
                raise FileNotFound(f"Unable to stream file: {e}") from e

    def get_file_info_sync(self, message: Message) -> dict[str, Any]:
        media = get_media(message)
        if not media:
            return {"message_id": message.id, "error": "No media"}

        media_type = type(media).__name__.lower()
        file_name = getattr(media, "file_name", None)
        mime_type = getattr(media, "mime_type", None)

        if not file_name:
            ext, _ = ext_and_mime_for_class(media_type)
            file_name = f"Thunder_{message.id}.{ext}"

        if not mime_type:
            _, mime_type = ext_and_mime_for_class(media_type)

        return {
            "message_id": message.id,
            "file_size": getattr(media, "file_size", 0) or 0,
            "file_name": file_name,
            "mime_type": mime_type,
            "unique_id": getattr(media, "file_unique_id", None),
            "media_type": media_type,
        }

    async def get_file_info(self, message_id: int) -> dict[str, Any]:
        try:
            message = await self.get_message(message_id)
            return self.get_file_info_sync(message)
        except Exception as e:
            logger.debug(f"Error getting file info for {message_id}: {e}", exc_info=True)
            return {"message_id": message_id, "error": str(e)}
