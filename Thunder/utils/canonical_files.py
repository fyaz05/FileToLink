import asyncio
import datetime
import hashlib
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from pyrogram.errors import FloodWait
from pyrogram.types import Message
from pymongo.errors import DuplicateKeyError

from Thunder.bot import StreamBot
from Thunder.utils.database import db
from Thunder.utils.file_properties import get_fname, get_media, get_uniqid
from Thunder.utils.logger import logger
from Thunder.vars import Var

PUBLIC_HASH_LENGTH = 20
_CACHE_TTL_SECONDS = 600
_CACHE_MAX_ITEMS = 4096
_INGEST_CLAIM_TTL_SECONDS = 60
_INGEST_CLAIM_WAIT_SECONDS = 15
_INGEST_CLAIM_POLL_SECONDS = 0.5
_MAX_INGEST_RETRIES = 10
_CACHE_PRUNE_INTERVAL = 50

_cache_by_unique_id: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_cache_by_hash: "OrderedDict[str, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_cache_by_message_id: "OrderedDict[int, Tuple[float, Dict[str, Any]]]" = OrderedDict()

_upload_locks: dict[str, asyncio.Lock] = {}
_upload_lock_counts: dict[str, int] = {}
_upload_locks_guard = asyncio.Lock()
_insert_counter: int = 0
_pending_touches: Dict[str, Tuple[Dict[str, Any], bool]] = {}
_flush_task: Optional[asyncio.Task] = None
_FLUSH_DELAY_SECONDS = 10


def build_public_hash(file_unique_id: str) -> str:
    return hashlib.sha256(file_unique_id.encode("utf-8")).hexdigest()[:PUBLIC_HASH_LENGTH]


def _infer_mime_type(media: Any) -> str:
    mime_type = getattr(media, "mime_type", None)
    if mime_type:
        return mime_type

    mime_map = {
        "photo": "image/jpeg",
        "voice": "audio/ogg",
        "videonote": "video/mp4",
    }
    return mime_map.get(type(media).__name__.lower(), "application/octet-stream")


def build_file_record(
    stored_message: Message,
    *,
    source_chat_id: Optional[int] = None,
    source_message_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    media = get_media(stored_message)
    file_unique_id = get_uniqid(stored_message)
    if not media or not file_unique_id:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "file_unique_id": file_unique_id,
        "public_hash": build_public_hash(file_unique_id),
        "canonical_message_id": stored_message.id,
        "file_id": getattr(media, "file_id", None),
        "file_name": get_fname(stored_message),
        "mime_type": _infer_mime_type(media),
        "file_size": getattr(media, "file_size", 0) or 0,
        "media_type": type(media).__name__.lower(),
        "first_source_chat_id": source_chat_id,
        "first_source_message_id": source_message_id,
        "created_at": now,
        "last_seen_at": now,
        "seen_count": 1,
        "reuse_count": 0
    }


def _prune_cache(cache: "OrderedDict[Any, Tuple[float, Dict[str, Any]]]") -> None:
    now = asyncio.get_running_loop().time()
    expired_keys = [key for key, (ts, _) in cache.items() if now - ts > _CACHE_TTL_SECONDS]
    for key in expired_keys:
        cache.pop(key, None)
    while len(cache) > _CACHE_MAX_ITEMS:
        cache.popitem(last=False)


def _cache_get(
    cache: "OrderedDict[Any, Tuple[float, Dict[str, Any]]]",
    key: Any
) -> Optional[Dict[str, Any]]:
    if key not in cache:
        return None
    ts, value = cache[key]
    now = asyncio.get_running_loop().time()
    if now - ts > _CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    cache.move_to_end(key)
    return value


def _remember(record: Dict[str, Any]) -> Dict[str, Any]:
    global _insert_counter
    now = asyncio.get_running_loop().time()
    file_unique_id = record.get("file_unique_id")
    public_hash = record.get("public_hash")
    canonical_message_id = record.get("canonical_message_id")

    _insert_counter += 1
    should_prune = (_insert_counter % _CACHE_PRUNE_INTERVAL == 0)

    if file_unique_id:
        _cache_by_unique_id[file_unique_id] = (now, record)
        _cache_by_unique_id.move_to_end(file_unique_id)
        if should_prune:
            _prune_cache(_cache_by_unique_id)
    if public_hash:
        _cache_by_hash[public_hash] = (now, record)
        _cache_by_hash.move_to_end(public_hash)
        if should_prune:
            _prune_cache(_cache_by_hash)
    if canonical_message_id is not None:
        _cache_by_message_id[canonical_message_id] = (now, record)
        _cache_by_message_id.move_to_end(canonical_message_id)
        if should_prune:
            _prune_cache(_cache_by_message_id)
    return record


def _forget(record: Dict[str, Any]) -> None:
    file_unique_id = record.get("file_unique_id")
    public_hash = record.get("public_hash")
    canonical_message_id = record.get("canonical_message_id")

    if file_unique_id:
        _cache_by_unique_id.pop(file_unique_id, None)
    if public_hash:
        _cache_by_hash.pop(public_hash, None)
    if canonical_message_id is not None:
        _cache_by_message_id.pop(canonical_message_id, None)


async def get_file_by_unique_id(file_unique_id: str) -> Optional[Dict[str, Any]]:
    cached = _cache_get(_cache_by_unique_id, file_unique_id)
    if cached:
        return cached
    record = await db.get_file_by_unique_id(file_unique_id)
    return _remember(record) if record else None


async def get_file_by_hash(
    public_hash: str,
    *,
    raise_on_error: bool = True
) -> Optional[Dict[str, Any]]:
    cached = _cache_get(_cache_by_hash, public_hash)
    if cached:
        return cached
    record = await db.get_file_by_hash(public_hash, raise_on_error=raise_on_error)
    return _remember(record) if record else None


async def get_file_by_message_id(canonical_message_id: int) -> Optional[Dict[str, Any]]:
    cached = _cache_get(_cache_by_message_id, canonical_message_id)
    if cached:
        return cached
    record = await db.get_file_by_message_id(canonical_message_id)
    return _remember(record) if record else None


async def touch_file_record(record: Dict[str, Any], *, reused: bool = False) -> None:
    if not record.get("public_hash"):
        return
    record["last_seen_at"] = datetime.datetime.now(datetime.timezone.utc)
    record["seen_count"] = int(record.get("seen_count", 0)) + 1
    if reused:
        record["reuse_count"] = int(record.get("reuse_count", 0)) + 1
    _remember(record)
    await db.touch_file_record(record["public_hash"], reused=reused, raise_on_error=True)


async def _flush_pending_touches() -> None:
    global _flush_task
    flushed = False
    try:
        await asyncio.sleep(_FLUSH_DELAY_SECONDS)
        
        items = list(_pending_touches.items())
        _pending_touches.clear()
            
        for public_hash, (record, reused) in items:
            try:
                await db.touch_file_record(public_hash, reused=reused)
            except Exception as e:
                logger.error(f"Failed to flush touch for {public_hash}: {e}", exc_info=True)
        flushed = True
    except asyncio.CancelledError:
        pass
    finally:
        if not flushed and _pending_touches:
            items = list(_pending_touches.items())
            _pending_touches.clear()
                
            for public_hash, (record, reused) in items:
                try:
                    await db.touch_file_record(public_hash, reused=reused)
                except Exception as e:
                    logger.error(f"Failed to flush touch for {public_hash}: {e}", exc_info=True)
        _flush_task = None


def schedule_touch_file_record(record: Dict[str, Any], *, reused: bool = False) -> None:
    global _flush_task
    if not record.get("public_hash"):
        return

    record["last_seen_at"] = datetime.datetime.now(datetime.timezone.utc)
    record["seen_count"] = int(record.get("seen_count", 0)) + 1
    if reused:
        record["reuse_count"] = int(record.get("reuse_count", 0)) + 1
    _remember(record)

    public_hash = record["public_hash"]
    if public_hash in _pending_touches:
        _, existing_reused = _pending_touches[public_hash]
        _pending_touches[public_hash] = (record, existing_reused or reused)
    else:
        _pending_touches[public_hash] = (record, reused)

    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_pending_touches())


async def drain_background_touch_tasks() -> None:
    if _flush_task and not _flush_task.done():
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass
            
    items = list(_pending_touches.items())
    _pending_touches.clear()
        
    for public_hash, (record, reused) in items:
        try:
            await db.touch_file_record(public_hash, reused=reused)
        except Exception as e:
            logger.error(f"Failed to flush touch for {public_hash}: {e}", exc_info=True)


async def update_cached_file_id(record: Dict[str, Any], file_id: str) -> None:
    if not record.get("public_hash") or not file_id:
        return
    record["file_id"] = file_id
    _remember(record)
    await db.update_file_id(record["public_hash"], file_id, raise_on_error=True)


async def _fetch_canonical_message(record: Dict[str, Any]) -> Optional[Message]:
    canonical_message_id = record.get("canonical_message_id")
    if canonical_message_id is None:
        return None

    try:
        try:
            message = await StreamBot.get_messages(
                chat_id=int(Var.BIN_CHANNEL),
                message_ids=int(canonical_message_id)
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            message = await StreamBot.get_messages(
                chat_id=int(Var.BIN_CHANNEL),
                message_ids=int(canonical_message_id)
            )
    except Exception as e:
        logger.warning(
            f"Error fetching canonical message {canonical_message_id}: {e}",
            exc_info=True
        )
        raise

    if not message or not message.media:
        return None
    return message


async def _is_canonical_record_valid(record: Dict[str, Any], file_unique_id: str) -> bool:
    message = await _fetch_canonical_message(record)
    return bool(message and get_uniqid(message) == file_unique_id)


async def _get_reusable_canonical_record(
    file_unique_id: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    existing = await get_file_by_unique_id(file_unique_id)
    if not existing:
        return None, None

    try:
        is_valid = await _is_canonical_record_valid(existing, file_unique_id)
    except Exception as e:
        logger.warning(
            f"Falling back to BIN re-copy for {file_unique_id} after canonical validation failed: {e}",
            exc_info=True
        )
        is_valid = False

    if is_valid:
        return existing, None

    _forget(existing)
    return None, existing


async def _wait_for_other_worker_canonical_record(file_unique_id: str) -> Optional[Dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INGEST_CLAIM_WAIT_SECONDS

    while loop.time() < deadline:
        reusable_record, _ = await _get_reusable_canonical_record(file_unique_id)
        if reusable_record:
            return reusable_record

        if not await db.is_file_ingest_claim_active(file_unique_id):
            break

        await asyncio.sleep(_INGEST_CLAIM_POLL_SECONDS)

    return None


def _merge_replacement_record(
    existing: Dict[str, Any],
    refreshed: Dict[str, Any]
) -> Dict[str, Any]:
    refreshed["created_at"] = existing.get("created_at", refreshed["created_at"])
    refreshed["seen_count"] = int(existing.get("seen_count", 0)) + 1
    refreshed["reuse_count"] = int(existing.get("reuse_count", 0))
    refreshed["first_source_chat_id"] = existing.get(
        "first_source_chat_id",
        refreshed.get("first_source_chat_id")
    )
    refreshed["first_source_message_id"] = existing.get(
        "first_source_message_id",
        refreshed.get("first_source_message_id")
    )
    return refreshed


@asynccontextmanager
async def file_ingest_lock(file_unique_id: str):
    async with _upload_locks_guard:
        lock = _upload_locks.get(file_unique_id)
        if lock is None:
            lock = asyncio.Lock()
            _upload_locks[file_unique_id] = lock
            _upload_lock_counts[file_unique_id] = 0
        _upload_lock_counts[file_unique_id] += 1

    acquired = False
    try:
        await lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            lock.release()
        async with _upload_locks_guard:
            remaining = _upload_lock_counts.get(file_unique_id, 1) - 1
            if remaining <= 0:
                _upload_lock_counts.pop(file_unique_id, None)
                _upload_locks.pop(file_unique_id, None)
            else:
                _upload_lock_counts[file_unique_id] = remaining


async def get_or_create_canonical_file(
    source_message: Message,
    copy_media: Callable[[Message], Awaitable[Optional[Message]]]
) -> Tuple[Optional[Dict[str, Any]], Optional[Message], bool]:
    file_unique_id = get_uniqid(source_message)
    if not file_unique_id:
        return None, None, False

    async with file_ingest_lock(file_unique_id):
        for _attempt in range(_MAX_INGEST_RETRIES):
            if _attempt > 0:
                await asyncio.sleep(min(0.5 * (2 ** (_attempt - 1)), 5.0))
            
            reusable_record, stale_record = await _get_reusable_canonical_record(file_unique_id)
            if reusable_record:
                schedule_touch_file_record(reusable_record, reused=True)
                return reusable_record, None, True

            claim_acquired = await db.acquire_file_ingest_claim(
                file_unique_id,
                ttl_seconds=_INGEST_CLAIM_TTL_SECONDS
            )
            if not claim_acquired:
                reusable_record = await _wait_for_other_worker_canonical_record(file_unique_id)
                if reusable_record:
                    schedule_touch_file_record(reusable_record, reused=True)
                    return reusable_record, None, True
                continue

            try:
                reusable_record, stale_record = await _get_reusable_canonical_record(file_unique_id)
                if reusable_record:
                    schedule_touch_file_record(reusable_record, reused=True)
                    return reusable_record, None, True

                stored_message = await copy_media(source_message)
                if not stored_message:
                    return None, None, False

                record = build_file_record(
                    stored_message,
                    source_chat_id=source_message.chat.id if source_message.chat else None,
                    source_message_id=source_message.id
                )
                if not record:
                    return None, stored_message, False

                try:
                    if stale_record:
                        record = _merge_replacement_record(stale_record, record)
                        await db.replace_file_record(record)
                    else:
                        await db.create_file_record(record)
                    _remember(record)
                    return record, stored_message, False
                except DuplicateKeyError:
                    reusable_record = await _wait_for_other_worker_canonical_record(file_unique_id)
                    if reusable_record:
                        schedule_touch_file_record(reusable_record, reused=True)
                        return reusable_record, stored_message, True
                    if stored_message:
                        try:
                            await stored_message.delete()
                        except Exception as e:
                            logger.warning(f"Failed to delete stored message {stored_message.id} in BIN_CHANNEL: {e}", exc_info=True)
                    raise
                except FloodWait:
                    raise
                except Exception as e:
                    logger.error(f"Error creating canonical file for {file_unique_id}: {e}", exc_info=True)
                    return None, stored_message, False
            finally:
                await db.release_file_ingest_claim(file_unique_id)

        logger.error(f"Max ingest retries ({_MAX_INGEST_RETRIES}) exhausted for {file_unique_id}")
        return None, None, False
