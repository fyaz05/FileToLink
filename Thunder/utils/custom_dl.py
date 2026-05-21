from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import aiofiles
import pytdbot
from pytdbot import types

from Thunder.server.exceptions import FileNotFound
from Thunder.utils.compat import _get_file_name, _get_media_file, _get_mime_type
from Thunder.utils.logger import logger
from Thunder.utils.media_helpers import _get_extension_for_content_type, _infer_mime_from_content_type
from Thunder.vars import Var

_DOWNLOAD_DIR = os.path.join(tempfile.gettempdir(), "thunder_downloads")
os.makedirs(_DOWNLOAD_DIR, exist_ok=True)


class ByteStreamer:
    __slots__ = ('client', 'chat_id')

    def __init__(self, client: pytdbot.Client) -> None:
        self.client = client
        self.chat_id = int(Var.BIN_CHANNEL)

    async def get_message(self, message_id: int) -> types.Message:
        result = await self.client.getMessage(
            chat_id=self.chat_id, message_id=message_id
        )
        if isinstance(result, types.Error):
            raise FileNotFound(f"Message {message_id} not found: {result.message}")
        if not result or not hasattr(result, "content") or result.content is None:
            raise FileNotFound(f"Message {message_id} not found")
        if not self._extract_media_file(result):
            raise FileNotFound(f"Message {message_id} has no media")
        return result

    @staticmethod
    def _cleanup_file(path: str) -> None:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Failed to clean up temp file {path}: {e}")

    @staticmethod
    def _extract_media_file(message: types.Message) -> types.File | None:
        return _get_media_file(message)

    async def _download_file(self, file_id: int) -> str:
        result = await self.client.downloadFile(
            file_id=file_id, priority=1, synchronous=True
        )
        if isinstance(result, types.Error):
            raise FileNotFound(f"Download failed: {result.message}")
        if not result.local.is_downloading_completed:
            raise FileNotFound(f"Download incomplete for file {file_id}")
        return result.local.path

    async def stream_file(
        self,
        media_ref: int | types.Message,
        offset: int = 0,
        limit: int = 0,
        fallback_message_id: int | None = None,
        on_fallback_message: Callable[[types.Message], Awaitable[None]] | None = None,
    ) -> AsyncGenerator[bytes]:
        refs: list[int] = []
        if isinstance(media_ref, types.Message):
            refs.append(media_ref.id)
        elif isinstance(media_ref, int):
            refs.append(media_ref)

        if fallback_message_id is not None and fallback_message_id not in refs:
            refs.append(fallback_message_id)

        last_error: Exception | None = None
        for ref in refs:
            file_path: str | None = None
            try:
                message = await self.get_message(ref)
                if on_fallback_message is not None and ref == fallback_message_id:
                    await on_fallback_message(message)

                media_file = self._extract_media_file(message)
                if not media_file:
                    raise FileNotFound(f"No media in message {ref}")

                file_path = await self._download_file(media_file.id)
                file_size = media_file.size

                read_offset = max(0, offset)
                bytes_remaining = (file_size - read_offset) if limit <= 0 else min(limit, file_size - read_offset)
                chunk_size = 1024 * 1024  # 1 MB

                async with aiofiles.open(file_path, "rb") as f:
                    await f.seek(read_offset)
                    while bytes_remaining > 0:
                        to_read = min(chunk_size, bytes_remaining)
                        chunk = await f.read(to_read)
                        if not chunk:
                            break
                        yield chunk
                        bytes_remaining -= len(chunk)
                return
            except FileNotFound:
                last_error = FileNotFound(f"Unable to stream file for ref {ref}")
                continue
            except Exception as e:
                last_error = e
                logger.debug(f"Error streaming media ref {ref}: {e}", exc_info=True)
                continue
            finally:
                if file_path:
                    try:
                        await asyncio.to_thread(self._cleanup_file, file_path)
                    except Exception:
                        pass

        raise last_error or FileNotFound("Unable to stream file")

    def get_file_info_sync(self, message: types.Message) -> dict[str, Any]:
        media_file = self._extract_media_file(message)
        if not media_file:
            return {"message_id": message.id, "error": "No media"}

        content = message.content
        content_type = type(content).__name__.lower()
        media_type = content_type.replace("message", "", 1) if content_type.startswith("message") else content_type
        file_name = _get_file_name(message)
        mime_type = _get_mime_type(message)

        if not file_name:
            ext = _get_extension_for_content_type(media_type).lstrip(".")
            file_name = f"Thunder_{message.id}.{ext}"

        if not mime_type:
            inferred = _infer_mime_from_content_type(media_type)
            if inferred:
                mime_type = inferred
            else:
                mime_type = "application/octet-stream"

        return {
            "message_id": message.id,
            "file_size": media_file.size,
            "file_name": file_name,
            "mime_type": mime_type,
            "unique_id": getattr(message, "remote_unique_file_id", None),
            "media_type": media_type,
        }

    async def get_file_info(self, message_id: int) -> dict[str, Any]:
        try:
            message = await self.get_message(message_id)
            return self.get_file_info_sync(message)
        except Exception as e:
            logger.debug(f"Error getting file info for {message_id}: {e}", exc_info=True)
            return {"message_id": message_id, "error": str(e)}
