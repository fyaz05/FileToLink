# Thunder/utils/custom_dl.py

import asyncio
import random
import time
from typing import Union, AsyncGenerator, Optional, Any

from aiocache import Cache
from pyrogram import Client, utils, raw
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid, Timeout, FloodWait
from pyrogram.file_id import FileId, FileType, ThumbnailSource

from Thunder.vars import Var
from Thunder.bot import work_loads
from Thunder.server.exceptions import FileNotFound
from .file_properties import get_file_ids
from Thunder.utils.logger import logger

# Global lock for workload tracking
WORK_LOADS_LOCK = asyncio.Lock()


def record_metric(metric_name: str, value: float = 1.0) -> None:
    logger.debug(f"Metric: {metric_name} = {value}")


# A simple circuit breaker for session.send calls.
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 10, recovery_timeout: float = 5.0):
        # Increased threshold and shorter recovery timeout for less interruption
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.open_until: Optional[float] = None

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.open_until = time.time() + self.recovery_timeout
            logger.debug("Circuit breaker opened.")

    def record_success(self) -> None:
        self.failure_count = 0
        self.open_until = None

    def allow_request(self) -> bool:
        if self.open_until and time.time() < self.open_until:
            return False
        return True


_circuit_breaker = CircuitBreaker()


class ByteStreamer:
    def __init__(
        self,
        client: Client,
        retry_attempts: int = 6,
        retry_delay: float = 0.25,  # lowered initial delay to speed up minor retries
        backoff_multiplier: float = 1.5,  # gentler backoff than doubling
        send_timeout: float = 10.0,
        cache_ttl: float = 300.0,
        cache_maxsize: int = 1024,  # configuration only; not used by aiocache MEMORY backend
        cache_clean_interval: float = 1800.0,  # 30 minutes cache clean interval
        concurrent_chunks: int = 3  # >1 to try batching chunk requests concurrently
    ):
        self.client = client
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.backoff_multiplier = backoff_multiplier
        self.send_timeout = send_timeout
        self.cache_ttl = cache_ttl
        self.concurrent_chunks = concurrent_chunks
        self._cache = Cache(Cache.MEMORY, ttl=cache_ttl)
        self._media_sessions_lock = asyncio.Lock()
        self.cache_clean_interval = cache_clean_interval
        # Start the background cache cleaning task.
        asyncio.create_task(self.clean_cache())

    async def clean_cache(self) -> None:
        """
        Periodically clears the cache to reduce memory usage.
        """
        while True:
            await asyncio.sleep(self.cache_clean_interval)
            await self._cache.clear()
            logger.debug("Cache cleaned.")

    async def get_file_properties(self, message_id: int) -> FileId:
        task = await self._cache.get(message_id)
        if task is not None:
            record_metric("cache_hit")
        else:
            record_metric("cache_miss")
            task = asyncio.create_task(self.generate_file_properties(message_id))
            await self._cache.set(message_id, task)
        try:
            return await task
        except Exception:
            await self._cache.delete(message_id)
            raise

    async def generate_file_properties(self, message_id: int) -> FileId:
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, message_id)
        if not file_id:
            raise FileNotFound(f"File with message ID {message_id} not found")
        return file_id

    async def generate_media_session(self, file_id: FileId) -> Session:
        async with self._media_sessions_lock:
            session = self.client.media_sessions.get(file_id.dc_id)
            if session:
                return session

        # Create a new media session.
        if file_id.dc_id != await self.client.storage.dc_id():
            auth_instance = Auth(self.client, file_id.dc_id, await self.client.storage.test_mode())
            session = Session(
                self.client,
                file_id.dc_id,
                await auth_instance.create(),
                await self.client.storage.test_mode(),
                is_media=True,
            )
            await session.start()
            delay = self.retry_delay
            for attempt in range(self.retry_attempts):
                try:
                    exported_auth = await self.client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=file_id.dc_id)
                    )
                    await session.send(
                        raw.functions.auth.ImportAuthorization(
                            id=exported_auth.id,
                            bytes=exported_auth.bytes
                        )
                    )
                    break
                except AuthBytesInvalid:
                    jitter = random.uniform(0, delay)
                    await asyncio.sleep(delay + jitter)
                    delay *= self.backoff_multiplier
            else:
                await session.stop()
                raise AuthBytesInvalid(f"Failed to import authorization for DC {file_id.dc_id}")
        else:
            session = Session(
                self.client,
                file_id.dc_id,
                await self.client.storage.auth_key(),
                await self.client.storage.test_mode(),
                is_media=True,
            )
            await session.start()
        async with self._media_sessions_lock:
            self.client.media_sessions[file_id.dc_id] = session
        return session

    async def get_location(self, file_id: FileId) -> Optional[Union[
        raw.types.InputPhotoFileLocation,
        raw.types.InputDocumentFileLocation,
        raw.types.InputPeerPhotoFileLocation,
    ]]:
        if file_id.file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(user_id=file_id.chat_id, access_hash=file_id.chat_access_hash)
            else:
                peer = (raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                        if file_id.chat_access_hash == 0
                        else raw.types.InputPeerChannel(
                            channel_id=utils.get_channel_id(file_id.chat_id),
                            access_hash=file_id.chat_access_hash,
                        ))
            return raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=(file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG),
            )
        elif file_id.file_type == FileType.PHOTO:
            if not file_id.file_reference:
                logger.info("No URI: file_reference missing for PHOTO type. Ending download.")
                return None
            return raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            if not file_id.file_reference:
                logger.info("No URI: file_reference missing for DOCUMENT type. Ending download.")
                return None
            return raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )

    async def _retry_session_send(self, session: Session, request_obj: Any, file_id: FileId) -> Any:
        if not _circuit_breaker.allow_request():
            logger.debug("Circuit breaker open; aborting send.")
            raise Timeout("Circuit breaker open.")
        delay = self.retry_delay
        latencies = []
        for attempt in range(self.retry_attempts):
            start_time = time.time()
            try:
                result = await asyncio.wait_for(session.send(request_obj), timeout=self.send_timeout)
                latency = time.time() - start_time
                latencies.append(latency)
                _circuit_breaker.record_success()
                return result
            except (Timeout, TimeoutError) as e:
                record_metric("send_timeout", 1)
                logger.debug(f"Timeout attempt {attempt + 1}: {e}")
                _circuit_breaker.record_failure()
                # Continue the loop to allow the backoff to run.
            except FloodWait as e:
                record_metric("flood_wait", 1)
                wait_time: Optional[float] = getattr(e, "x", None)
                if wait_time is None:
                    wait_time = 2.0
                logger.debug(f"FloodWait: waiting {wait_time} seconds.")
                await asyncio.sleep(wait_time)
                continue
            except OSError as e:
                if "closed" in str(e).lower():
                    record_metric("connection_closed", 1)
                    logger.debug("Connection closed detected; reinitializing session.")
                    session = await self.generate_media_session(file_id)
                    continue
                else:
                    raise
            # Apply jitter and backoff for each retry.
            jitter = random.uniform(0, delay)
            await asyncio.sleep(delay + jitter)
            delay *= self.backoff_multiplier

        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            logger.debug(f"Avg latency: {avg_latency:.2f}s")
        # If we get here, all attempts failed. Consider closing the session.
        try:
            await session.stop()
            logger.debug("Stopped session after repeated failures.")
        except Exception:
            pass
        raise Timeout("Session.send failed after retries.")

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> AsyncGenerator[bytes, None]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if part_count <= 0:
            raise ValueError("part_count must be positive")
        if first_part_cut < 0 or last_part_cut < 0:
            raise ValueError("Cut indices must be non-negative")
        if first_part_cut > last_part_cut:
            first_part_cut, last_part_cut = last_part_cut, first_part_cut

        async with WORK_LOADS_LOCK:
            work_loads[index] += 1
            record_metric("workload_incr")

        session = await self.generate_media_session(file_id)
        current_part = 1
        location = await self.get_location(file_id)
        if location is None:
            async with WORK_LOADS_LOCK:
                work_loads[index] -= 1
                record_metric("workload_decr")
            return

        while True:
            req = raw.functions.upload.GetFile(location=location, offset=offset, limit=chunk_size)
            try:
                if self.concurrent_chunks > 1:
                    tasks = [self._retry_session_send(session, req, file_id) for _ in range(self.concurrent_chunks)]
                    responses = await asyncio.gather(*tasks, return_exceptions=True)
                    response = next((r for r in responses if not isinstance(r, Exception)), None)
                    if response is None:
                        break
                else:
                    response = await self._retry_session_send(session, req, file_id)
            except (Timeout, TimeoutError) as e:
                logger.debug(f"Timeout during chunk fetch: {e}")
                break
            except AttributeError as e:
                logger.debug(f"Attribute error during chunk fetch: {e}")
                break

            if not isinstance(response, raw.types.upload.File):
                break

            chunk = response.bytes
            if not chunk:
                break
            c_len = len(chunk)
            if part_count == 1:
                yield chunk[min(first_part_cut, c_len):min(last_part_cut, c_len)]
            elif current_part == 1:
                yield chunk[min(first_part_cut, c_len):]
            elif current_part == part_count:
                yield chunk[:min(last_part_cut, c_len)]
            else:
                yield chunk

            current_part += 1
            offset += chunk_size
            if current_part > part_count:
                break

        async with WORK_LOADS_LOCK:
            work_loads[index] -= 1
            record_metric("workload_decr")

    async def check_session_health(self) -> None:
        async with self._media_sessions_lock:
            for dc_id, session in self.client.media_sessions.items():
                try:
                    await session.send(raw.functions.help.GetConfig())
                    record_metric("session_healthy", 1)
                except Exception as e:
                    logger.warning(f"Session for DC {dc_id} unhealthy: {e}")
                    record_metric("session_unhealthy", 1)

    async def close_sessions(self) -> None:
        async with self._media_sessions_lock:
            for session in self.client.media_sessions.values():
                try:
                    await session.stop()
                except Exception:
                    pass
            self.client.media_sessions.clear()
            record_metric("sessions_closed", 0)