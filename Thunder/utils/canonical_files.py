import asyncio
import datetime
import hashlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from pymongo.errors import DuplicateKeyError
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from Thunder.utils.database import db
from Thunder.utils.file_properties import get_fname, get_media, get_uniqid
from Thunder.utils.logger import logger
from Thunder.utils.safe_call import tg_call
from Thunder.vars import Var

# L4: new ingestions hash to 32 hex chars; the historical 20-char family
# stays valid forever so every existing link keeps working.
PUBLIC_HASH_LENGTH = 32
LEGACY_PUBLIC_HASH_LENGTH = 20
_CACHE_TTL_SECONDS = 600
_CACHE_MAX_ITEMS = 4096
_INGEST_CLAIM_TTL_SECONDS = 60
_INGEST_CLAIM_WAIT_SECONDS = 15
_INGEST_CLAIM_POLL_SECONDS = 0.5
_MAX_INGEST_RETRIES = 10
_CACHE_PRUNE_INTERVAL = 50

# M14: bounded touch buffer -- overflow drops increments (counted) instead
# of growing memory; flushes batch into a single BulkWrite.
_FLUSH_DELAY_SECONDS = max(1, min(60, int(getattr(Var, "TOUCH_FLUSH_SECONDS", 3))))
_TOUCH_BUFFER_MAX = max(100, int(getattr(Var, "TOUCH_BUFFER_MAX", 1000)))
_dropped_touches = 0

_cache_by_unique_id: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
_cache_by_hash: "OrderedDict[str, tuple[float, dict[str, Any]]]" = OrderedDict()
_cache_by_message_id: "OrderedDict[int, tuple[float, dict[str, Any]]]" = OrderedDict()

_upload_locks: dict[str, asyncio.Lock] = {}
_upload_lock_counts: dict[str, int] = {}
_upload_locks_guard = asyncio.Lock()
_insert_counter: int = 0
_pending_touches: dict[str, tuple[dict[str, Any], bool]] = {}
_flush_task: asyncio.Task | None = None


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
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
) -> dict[str, Any] | None:
    media = get_media(stored_message)
    file_unique_id = get_uniqid(stored_message)
    if not media or not file_unique_id:
        return None

    now = datetime.datetime.now(datetime.UTC)
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
        "reuse_count": 0,
    }


def _prune_cache(cache: "OrderedDict[Any, tuple[float, dict[str, Any]]]") -> None:
    now = asyncio.get_running_loop().time()
    expired_keys = [key for key, (ts, _) in cache.items() if now - ts > _CACHE_TTL_SECONDS]
    for key in expired_keys:
        cache.pop(key, None)
    while len(cache) > _CACHE_MAX_ITEMS:
        cache.popitem(last=False)


def _cache_get(
    cache: "OrderedDict[Any, tuple[float, dict[str, Any]]]", key: Any
) -> dict[str, Any] | None:
    if key not in cache:
        return None
    ts, value = cache[key]
    now = asyncio.get_running_loop().time()
    if now - ts > _CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    cache.move_to_end(key)
    return value


def _remember(record: dict[str, Any]) -> dict[str, Any]:
    global _insert_counter
    now = asyncio.get_running_loop().time()
    file_unique_id = record.get("file_unique_id")
    public_hash = record.get("public_hash")
    canonical_message_id = record.get("canonical_message_id")

    _insert_counter += 1
    should_prune = _insert_counter % _CACHE_PRUNE_INTERVAL == 0

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


def _forget(record: dict[str, Any]) -> None:
    file_unique_id = record.get("file_unique_id")
    public_hash = record.get("public_hash")
    canonical_message_id = record.get("canonical_message_id")

    if file_unique_id:
        _cache_by_unique_id.pop(file_unique_id, None)
    if public_hash:
        _cache_by_hash.pop(public_hash, None)
    if canonical_message_id is not None:
        _cache_by_message_id.pop(canonical_message_id, None)


async def get_file_by_unique_id(file_unique_id: str) -> dict[str, Any] | None:
    cached = _cache_get(_cache_by_unique_id, file_unique_id)
    if cached:
        return cached
    record = await db.get_file_by_unique_id(file_unique_id)
    return _remember(record) if record else None


async def get_file_by_hash(
    public_hash: str, *, raise_on_error: bool = True
) -> dict[str, Any] | None:
    cached = _cache_get(_cache_by_hash, public_hash)
    if cached:
        return cached
    record = await db.get_file_by_hash(public_hash, raise_on_error=raise_on_error)
    return _remember(record) if record else None


async def get_file_by_message_id(canonical_message_id: int) -> dict[str, Any] | None:
    cached = _cache_get(_cache_by_message_id, canonical_message_id)
    if cached:
        return cached
    return None


async def forget_stale_record(record: dict[str, Any]) -> bool:
    """Self-healing (M10): drop a corrupted/stale record from cache + DB so
    the next upload re-ingests cleanly instead of erroring forever."""
    public_hash = record.get("public_hash")
    if not public_hash:
        return False
    _forget(record)
    deleted = await db.delete_file_record(public_hash)
    logger.warning(f"Self-healed stale file record {public_hash} (deleted={deleted})")
    return deleted


async def _flush_pending_touches() -> None:
    global _flush_task, _dropped_touches
    flushed = False
    try:
        await asyncio.sleep(_FLUSH_DELAY_SECONDS)
        await _bulk_flush()
        flushed = True
    except asyncio.CancelledError:
        pass
    finally:
        if not flushed and _pending_touches:
            try:
                await _bulk_flush()
            except Exception as e:
                logger.error(f"Touch flush failed on cancel path: {e}", exc_info=True)
        _flush_task = None


async def _bulk_flush() -> None:
    items = list(_pending_touches.items())
    _pending_touches.clear()
    if not items:
        return
    try:
        await db.bulk_touch_file_records([(h, reused) for h, (_, reused) in items])
    except Exception as e:
        logger.error(f"Failed to bulk-flush {len(items)} touches: {e}", exc_info=True)


def schedule_touch_file_record(record: dict[str, Any], *, reused: bool = False) -> None:
    global _flush_task, _dropped_touches
    if not record.get("public_hash"):
        return

    record["last_seen_at"] = datetime.datetime.now(datetime.UTC)
    record["seen_count"] = int(record.get("seen_count", 0)) + 1
    if reused:
        record["reuse_count"] = int(record.get("reuse_count", 0)) + 1
    _remember(record)

    public_hash = record["public_hash"]
    if public_hash in _pending_touches:
        _, existing_reused = _pending_touches[public_hash]
        _pending_touches[public_hash] = (record, existing_reused or reused)
    elif len(_pending_touches) >= _TOUCH_BUFFER_MAX:
        # M14: drop-on-overflow with a counter -- memory stays capped
        _dropped_touches += 1
        if _dropped_touches % 100 == 1:
            logger.warning(
                f"Touch buffer full ({_TOUCH_BUFFER_MAX}); "
                f"dropped {_dropped_touches} increments so far"
            )
    else:
        _pending_touches[public_hash] = (record, reused)

    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_flush_pending_touches())


def touch_buffer_stats() -> dict[str, int]:
    return {
        "pending": len(_pending_touches),
        "dropped": _dropped_touches,
        "cap": _TOUCH_BUFFER_MAX,
    }


async def drain_background_touch_tasks() -> None:
    if _flush_task and not _flush_task.done():
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass

    try:
        await _bulk_flush()
    except Exception as e:
        logger.error(f"Failed to flush touch buffer at shutdown: {e}", exc_info=True)


async def update_cached_file_id(record: dict[str, Any], file_id: str) -> None:
    if not record.get("public_hash") or not file_id:
        return
    record["file_id"] = file_id
    _remember(record)
    await db.update_file_id(record["public_hash"], file_id, raise_on_error=True)


async def _fetch_canonical_message(record: dict[str, Any], client=None) -> Message | None:
    canonical_message_id = record.get("canonical_message_id")
    if canonical_message_id is None:
        return None

    # M12 layering break: the client is passed in by callers; the lazy
    # fallback keeps backward compatibility for existing call sites.
    if client is None:
        from Thunder.bot import StreamBot

        client = StreamBot

    try:
        message = await tg_call(
            client.get_messages,
            chat_id=int(Var.BIN_CHANNEL),
            message_ids=int(canonical_message_id),
            retries=1,
            timeout=60,
        )
    except Exception as e:
        logger.warning(
            f"Error fetching canonical message {canonical_message_id}: {e}", exc_info=True
        )
        raise

    if not message or not message.media:
        return None
    return message


async def _is_canonical_record_valid(
    record: dict[str, Any], file_unique_id: str, client=None
) -> bool:
    message = await _fetch_canonical_message(record, client)
    return bool(message and get_uniqid(message) == file_unique_id)


async def _get_reusable_canonical_record(
    file_unique_id: str,
    client=None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    existing = await get_file_by_unique_id(file_unique_id)
    if not existing:
        return None, None

    try:
        is_valid = await _is_canonical_record_valid(existing, file_unique_id, client)
    except Exception as e:
        logger.warning(
            f"Falling back to BIN re-copy for {file_unique_id} after canonical validation failed: {e}",
            exc_info=True,
        )
        is_valid = False

    if is_valid:
        return existing, None

    _forget(existing)
    return None, existing


async def _wait_for_other_worker_canonical_record(
    file_unique_id: str, client=None
) -> dict[str, Any] | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INGEST_CLAIM_WAIT_SECONDS

    while loop.time() < deadline:
        reusable_record, _ = await _get_reusable_canonical_record(file_unique_id, client)
        if reusable_record:
            return reusable_record

        if not await db.is_file_ingest_claim_active(file_unique_id):
            break

        await asyncio.sleep(_INGEST_CLAIM_POLL_SECONDS)

    return None


def _merge_replacement_record(
    existing: dict[str, Any], refreshed: dict[str, Any]
) -> dict[str, Any]:
    refreshed["created_at"] = existing.get("created_at", refreshed["created_at"])
    refreshed["seen_count"] = int(existing.get("seen_count", 0)) + 1
    refreshed["reuse_count"] = int(existing.get("reuse_count", 0))
    refreshed["first_source_chat_id"] = existing.get(
        "first_source_chat_id", refreshed.get("first_source_chat_id")
    )
    refreshed["first_source_message_id"] = existing.get(
        "first_source_message_id", refreshed.get("first_source_message_id")
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
    copy_media: Callable[[Message], Awaitable[Message | None]],
    client=None,
) -> tuple[dict[str, Any] | None, Message | None, bool]:
    file_unique_id = get_uniqid(source_message)
    if not file_unique_id:
        return None, None, False

    async with file_ingest_lock(file_unique_id):
        for _attempt in range(_MAX_INGEST_RETRIES):
            if _attempt > 0:
                await asyncio.sleep(min(0.5 * (2 ** (_attempt - 1)), 5.0))

            reusable_record, stale_record = await _get_reusable_canonical_record(
                file_unique_id, client
            )
            if reusable_record:
                schedule_touch_file_record(reusable_record, reused=True)
                return reusable_record, None, True

            claim_acquired = await db.acquire_file_ingest_claim(
                file_unique_id, ttl_seconds=_INGEST_CLAIM_TTL_SECONDS
            )
            if not claim_acquired:
                reusable_record = await _wait_for_other_worker_canonical_record(
                    file_unique_id, client
                )
                if reusable_record:
                    schedule_touch_file_record(reusable_record, reused=True)
                    return reusable_record, None, True
                continue

            try:
                reusable_record, stale_record = await _get_reusable_canonical_record(
                    file_unique_id, client
                )
                if reusable_record:
                    schedule_touch_file_record(reusable_record, reused=True)
                    return reusable_record, None, True

                stored_message = await copy_media(source_message)
                if not stored_message:
                    return None, None, False

                record = build_file_record(
                    stored_message,
                    source_chat_id=source_message.chat.id if source_message.chat else None,
                    source_message_id=source_message.id,
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
                    reusable_record = await _wait_for_other_worker_canonical_record(
                        file_unique_id, client
                    )
                    if reusable_record:
                        schedule_touch_file_record(reusable_record, reused=True)
                        return reusable_record, stored_message, True
                    if stored_message:
                        try:
                            await stored_message.delete()
                        except Exception as e:
                            logger.warning(
                                f"Failed to delete stored message {stored_message.id} in BIN_CHANNEL: {e}",
                                exc_info=True,
                            )
                    raise
                except FloodWait:
                    raise
                except Exception as e:
                    logger.error(
                        f"Error creating canonical file for {file_unique_id}: {e}", exc_info=True
                    )
                    return None, stored_message, False
            finally:
                await db.release_file_ingest_claim(file_unique_id)

        logger.error(f"Max ingest retries ({_MAX_INGEST_RETRIES}) exhausted for {file_unique_id}")
        return None, None, False
