# Thunder/utils/custom_dl.py

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from Thunder.server.exceptions import FileNotFound, TelegramUnavailable
from Thunder.utils.file_properties import get_media
from Thunder.utils.logger import logger
from Thunder.utils.media_types import ext_and_mime_for_class
from Thunder.utils.safe_call import tg_call
from Thunder.vars import Var

# M9/H4b: caps the total FloodWait pinning of one stream handler before a 503.
_MAX_STREAM_FLOODWAIT_SECONDS = 60.0


class ByteStreamer:
    __slots__ = ("client", "chat_id")

    def __init__(self, client: Client) -> None:
        self.client = client
        self.chat_id = int(Var.BIN_CHANNEL)

    async def get_message(self, message_id: int) -> Message:
        # H4b/H8: bounded FloodWait handling via tg_call; transient failures
        # raise TelegramUnavailable, NEVER FileNotFound -- the delivery route
        # self-heals (deletes the record) on FileNotFound.
        try:
            message = await tg_call(
                self.client.get_messages, self.chat_id, message_id, retries=2, timeout=60
            )
        except FloodWait as e:
            raise TelegramUnavailable(
                f"Telegram temporarily unavailable (FloodWait {e.value}s)"
            ) from e
        except Exception as e:
            logger.debug(f"Error fetching message {message_id}: {e}", exc_info=True)
            raise TelegramUnavailable(f"Telegram fetch failed for message {message_id}") from e

        # pyrogram's stubs declare `Message | list[Message]`, but a single
        # id always yields a single Message; a list here means bad input
        if isinstance(message, list) or not message or not message.media:
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

        # fetch the target ONCE, outside the retry loop: per-retry re-fetches
        # cost an extra RPC per FloodWait and turn hiccups into spurious 404s
        target = await self.get_message(media_ref) if isinstance(media_ref, int) else media_ref

        chunks_done = 0
        floodwait_sleep_total = 0.0
        while True:
            try:
                # stream_media is an async generator; stubs union it with
                # file_ref types, hence the ignore below
                async for chunk in self.client.stream_media(  # type: ignore[union-attr]
                    target, offset=chunk_offset, limit=chunk_limit
                ):
                    yield chunk
                    chunks_done += 1
                return
            except FloodWait as e:
                # resume from where the consumer is: restarting from the
                # original offset re-sends bytes and corrupts the download
                if chunks_done:
                    chunk_offset += chunks_done
                    if chunk_limit:
                        chunk_limit = max(chunk_limit - chunks_done, 0)
                    chunks_done = 0
                # bound total pinned time on sustained floods
                if floodwait_sleep_total + e.value > _MAX_STREAM_FLOODWAIT_SECONDS:
                    raise TelegramUnavailable(
                        "Sustained Telegram flood while streaming "
                        f">{_MAX_STREAM_FLOODWAIT_SECONDS:.0f}s; try again shortly"
                    ) from e
                floodwait_sleep_total += e.value
                logger.debug(f"FloodWait: stream_file, sleep {e.value}s")
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.debug(f"Error streaming media ref {media_ref}: {e}", exc_info=True)
                raise TelegramUnavailable(f"Unable to stream file: {e}") from e

    def get_file_info_sync(self, message: Message) -> dict[str, Any]:
        media = get_media(message)
        if not media:
            # the delivery ladder maps FileNotFound -> 404; an unreadable
            # "error" marker dict had no reader anywhere
            raise FileNotFound(f"Message {message.id} has no media")

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
        # no blanket swallow: the route's error ladder maps FileNotFound -> 404
        # and all else -> 500; masking here turned outages into misleading 404s
        message = await self.get_message(message_id)
        return self.get_file_info_sync(message)
