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
from Thunder.utils.safe_call import tg_call
from Thunder.vars import Var


class ByteStreamer:
    __slots__ = ("client", "chat_id")

    def __init__(self, client: Client) -> None:
        self.client = client
        self.chat_id = int(Var.BIN_CHANNEL)

    async def get_message(self, message_id: int) -> Message:
        # H4b/H8: bounded FloodWait handling via tg_call -- the previous
        # open-ended sleep loop could pin an HTTP handler (and its stream
        # slot) indefinitely on a sustained Telegram flood.
        try:
            message = await tg_call(
                self.client.get_messages, self.chat_id, message_id, retries=2, timeout=60
            )
        except FloodWait as e:
            raise FileNotFound(f"Message {message_id} unavailable (FloodWait {e.value}s)") from e
        except Exception as e:
            logger.debug(f"Error fetching message {message_id}: {e}", exc_info=True)
            raise FileNotFound(f"Message {message_id} not found") from e

        if isinstance(message, list):  # defensive: pyrogram returns a list for list inputs
            if not message:
                raise FileNotFound(f"Message {message_id} not found")
            message = message[0]
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
        chunks_done = 0
        while True:
            try:
                target = (
                    await self.get_message(media_ref) if isinstance(media_ref, int) else media_ref
                )
                # stream_media is an async generator in pyrofork; the stubs
                # union it with file_ref types, so narrow via ignore here.
                async for chunk in self.client.stream_media(  # type: ignore[union-attr]
                    target, offset=chunk_offset, limit=chunk_limit
                ):
                    yield chunk
                    chunks_done += 1
                return
            except FloodWait as e:
                # resume from where the CONSUMER actually is: restarting from
                # the original offset re-yields bytes already sent, corrupting
                # the download (duplicated middle, truncated tail).
                if chunks_done:
                    chunk_offset += chunks_done
                    if chunk_limit:
                        chunk_limit = max(chunk_limit - chunks_done, 0)
                    chunks_done = 0
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
