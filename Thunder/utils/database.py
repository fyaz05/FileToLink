# Thunder/utils/database.py

import datetime
import uuid
from typing import Any

from pymongo import AsyncMongoClient, UpdateOne
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, ExecutionTimeout, OperationFailure

from Thunder.utils.flag_cache import flags
from Thunder.utils.logger import logger
from Thunder.vars import Var

# H8: every Mongo op gets a server-side budget so a brownout cannot pin
# handlers forever; per-op overrides remain possible at call sites.
MONGO_TIMEOUT_MS = 5000


class Database:
    def __init__(self, uri: str, database_name: str, **kwargs):
        # tz_aware=True: pymongo's default returns naive UTC datetimes, which
        # raise TypeError against aware now(UTC) (broke token activation once)
        self._client = AsyncMongoClient(uri, timeoutMS=MONGO_TIMEOUT_MS, tz_aware=True, **kwargs)
        self.db = self._client[database_name]
        self.col: AsyncCollection = self.db.users
        self.banned_users_col: AsyncCollection = self.db.banned_users
        self.banned_channels_col: AsyncCollection = self.db.banned_channels
        self.token_col: AsyncCollection = self.db.tokens
        self.authorized_users_col: AsyncCollection = self.db.authorized_users
        self.restart_message_col: AsyncCollection = self.db.restart_message
        self.files_col: AsyncCollection = self.db.files
        self.file_ingest_locks_col: AsyncCollection = self.db.file_ingest_locks
        # One-shot migration markers (backfill completion bookkeeping)
        self.migration_flags_col: AsyncCollection = self.db.migration_flags

    async def _deduplicate_users(self) -> None:
        pipeline: list[dict[str, Any]] = [
            {"$sort": {"join_date": 1}},
            {"$group": {"_id": "$id", "doc_id": {"$first": "$_id"}}},
            {"$project": {"_id": "$doc_id"}},
        ]
        keep_ids = []
        # AsyncCollection.aggregate() is a coroutine in pymongo's async API:
        # iterate the awaited cursor, never the coroutine itself.
        cursor = await self.col.aggregate(pipeline)
        async for doc in cursor:
            keep_ids.append(doc["_id"])
        if keep_ids:
            result = await self.col.delete_many({"_id": {"$nin": keep_ids}})
            if result.deleted_count > 0:
                logger.warning(f"Deduplicated {result.deleted_count} duplicate user documents.")

    async def _file_ttl_index_seconds(self) -> int | None:
        """Current ``expireAfterSeconds`` of the file TTL index, or None when
        the index is absent (or its options cannot be inspected)."""
        try:
            # AsyncCollection.list_indexes() is a coroutine in pymongo's
            # async API: iterate the awaited cursor, never the coroutine.
            cursor = await self.files_col.list_indexes()
            async for idx in cursor:
                if idx.get("name") == "last_seen_at_1":
                    try:
                        return int(idx.get("expireAfterSeconds", -1))
                    except (TypeError, ValueError):
                        return -1
        except Exception as e:
            logger.warning(f"Could not inspect file TTL index: {e}")
        return None

    async def _backfill_done(self) -> bool:
        try:
            return bool(
                await self.migration_flags_col.find_one({"_id": "file_last_seen_backfill_done"})
            )
        except Exception:
            return False

    async def _mark_backfill_done(self) -> None:
        try:
            await self.migration_flags_col.update_one(
                {"_id": "file_last_seen_backfill_done"},
                {"$set": {"done_at": datetime.datetime.now(datetime.UTC)}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Could not record backfill completion marker: {e}")

    async def _get_backfill_cursor(self) -> Any:
        """Persisted resume position: without it every boot re-walks the
        already-stamped prefix and the migration never converges on large
        vaults."""
        try:
            doc = await self.migration_flags_col.find_one({"_id": "file_last_seen_backfill_cursor"})
            return doc.get("last_id") if doc else None
        except Exception:
            return None

    async def _save_backfill_cursor(self, last_id: Any) -> None:
        try:
            await self.migration_flags_col.update_one(
                {"_id": "file_last_seen_backfill_cursor"},
                {"$set": {"last_id": last_id}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"Could not persist backfill cursor: {e}")

    async def _backfill_file_last_seen(self) -> None:
        """One-off migration: stamp ``last_seen_at`` on legacy rows that lack
        it, so the TTL index activated right after gives them a full window.

        ``_id``-paged micro-batches filtered to unstamped rows keep each
        statement inside the 5s ``timeoutMS`` (a single ``update_many`` would
        COLLSCAN and abort the remaining index ensures).  The resume cursor is
        persisted per batch, so a capped or interrupted run continues where
        it stopped -- the filter shrinks as rows get stamped, guaranteeing
        convergence.  Must never raise.
        """
        stamp = datetime.datetime.now(datetime.UTC)
        batch_size = 500
        max_batches_per_boot = 100  # 50k unstamped rows per boot; converges across boots
        last_id: Any = await self._get_backfill_cursor()
        stamped = 0
        try:
            for _ in range(max_batches_per_boot):
                page_filter: dict[str, Any] = {"last_seen_at": {"$exists": False}}
                if last_id is not None:
                    page_filter["_id"] = {"$gt": last_id}
                page = (
                    await self.files_col.find(page_filter, {"_id": 1})
                    .sort("_id", 1)
                    .to_list(batch_size)
                )
                if not page:
                    await self._mark_backfill_done()
                    if stamped:
                        logger.info(f"Backfilled last_seen_at on {stamped} legacy file records.")
                    return
                last_id = page[-1]["_id"]
                await self.files_col.update_many(
                    {"_id": {"$in": [doc["_id"] for doc in page]}},
                    {"$set": {"last_seen_at": stamp}},
                )
                stamped += len(page)
                await self._save_backfill_cursor(last_id)
            logger.warning(
                "last_seen_at backfill hit the per-boot batch cap "
                f"({max_batches_per_boot} batches); continuing from the cursor next boot."
            )
        except Exception as e:
            # The migration must never abort the remaining index ensures.
            logger.warning(f"last_seen_at backfill interrupted (resumes from cursor): {e}")

    async def _create_file_ttl_index(self, expire_after_seconds: int) -> None:
        """Create (or recreate after an operator TTL change) the file TTL
        index without letting its failure modes abort the remaining ensures."""
        try:
            await self.files_col.create_index(
                "last_seen_at", expireAfterSeconds=expire_after_seconds
            )
        except ExecutionTimeout:
            # first build on a large vault can exceed the budget; the
            # server-side build continues and is idempotent, re-check next boot
            logger.warning(
                "File TTL index build exceeded the client timeout budget; "
                "the server-side build continues and is re-checked on next boot."
            )
        except OperationFailure as e:
            if e.code != 85:  # 85 = IndexOptionsConflict
                logger.warning(f"File TTL index creation failed: {e}")
                return
            # Mongo cannot alter TTL via createIndexes; an operator's
            # FILE_TTL_DAYS change must not abort the remaining unique ensures
            logger.warning("FILE_TTL_DAYS changed between boots; recreating file TTL index.")
            try:
                await self.files_col.drop_index("last_seen_at_1")
            except Exception:
                pass
            try:
                await self.files_col.create_index(
                    "last_seen_at", expireAfterSeconds=expire_after_seconds
                )
            except (ExecutionTimeout, OperationFailure) as e:
                # a concurrent recreate (or a server still building) must not
                # abort the remaining unique ensures
                logger.warning(f"File TTL index rebuild failed; re-checked on next boot: {e}")

    async def _ensure_index(self, col: AsyncCollection, keys: Any, **opts: Any) -> None:
        """Best-effort: one failed ensure (e.g. a build exceeding the client
        timeout on a large vault) must not abort the remaining ones -- the
        server-side build continues and the next boot re-runs it."""
        try:
            await col.create_index(keys, **opts)
        except (ExecutionTimeout, OperationFailure) as e:
            logger.warning(f"Index ensure on {col.name} skipped; re-checked on next boot: {e}")

    async def ensure_indexes(self, *, raise_on_error: bool = True) -> bool:
        try:
            # L2: backfill before the TTL index exists so pre-existing rows
            # get a full window instead of vanishing on activation (default off)
            if Var.FILE_TTL_DAYS > 0:
                expected_ttl = Var.FILE_TTL_DAYS * 86400
                current_ttl = await self._file_ttl_index_seconds()
                backfill_done = await self._backfill_done()
                if current_ttl == expected_ttl and backfill_done:
                    logger.debug(f"File TTL index already active: {Var.FILE_TTL_DAYS} days")
                else:
                    if not backfill_done:
                        # stamp legacy rows first so they get a full TTL window
                        await self._backfill_file_last_seen()
                    await self._create_file_ttl_index(expected_ttl)
                    logger.info(f"File TTL index active: {Var.FILE_TTL_DAYS} days")
            else:
                try:
                    await self.files_col.drop_index("last_seen_at_1")
                except Exception:
                    pass

            await self._ensure_index(self.banned_users_col, "user_id", unique=True)
            await self._ensure_index(self.banned_channels_col, "channel_id", unique=True)
            await self._ensure_index(self.token_col, "token", unique=True)
            # /start + generate() look tokens up by user; without this the
            # per-user scans walk the whole collection (H8-adjacent gap)
            await self._ensure_index(
                self.token_col, [("user_id", 1), ("activated", 1), ("expires_at", -1)]
            )
            await self._ensure_index(self.authorized_users_col, "user_id", unique=True)
            try:
                await self.col.create_index("id", unique=True)
            except DuplicateKeyError:
                logger.warning("Duplicate users found, deduplicating...")
                await self._deduplicate_users()
                await self._ensure_index(self.col, "id", unique=True)
            await self._ensure_index(self.token_col, "expires_at", expireAfterSeconds=0)
            await self._ensure_index(self.token_col, "activated")
            await self._ensure_index(self.restart_message_col, "message_id", unique=True)
            await self._ensure_index(self.restart_message_col, "timestamp", expireAfterSeconds=3600)
            await self._ensure_index(self.files_col, "file_unique_id", unique=True)
            await self._ensure_index(self.files_col, "public_hash", unique=True)
            await self._ensure_index(self.files_col, "canonical_message_id", unique=True)
            await self._ensure_index(self.files_col, "created_at")
            await self._ensure_index(self.file_ingest_locks_col, "expires_at", expireAfterSeconds=0)

            logger.debug("Database indexes ensured.")
            return True
        except Exception as e:
            logger.error(f"Error in ensure_indexes: {e}", exc_info=True)
            if raise_on_error:
                raise
            return False

    def new_user(self, user_id: int) -> dict:
        return {"id": user_id, "join_date": datetime.datetime.now(datetime.UTC)}

    async def add_user(self, user_id: int) -> bool:
        try:
            result = await self.col.update_one(
                {"id": user_id}, {"$setOnInsert": self.new_user(user_id)}, upsert=True
            )
            if result.upserted_id:
                logger.debug(f"Added new user {user_id} to database.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error in add_user for user {user_id}: {e}", exc_info=True)
            raise

    async def is_user_exist(self, user_id: int) -> bool:
        """Read-only existence check. For user registration, use add_user() instead."""
        try:
            user = await self.col.find_one({"id": user_id}, {"_id": 1})
            return bool(user)
        except Exception as e:
            logger.error(f"Error in is_user_exist for user {user_id}: {e}", exc_info=True)
            raise

    async def total_users_count(self) -> int:
        try:
            return await self.col.count_documents({})
        except Exception as e:
            logger.error(f"Error in total_users_count: {e}", exc_info=True)
            return 0

    async def get_authorized_users_count(self) -> int:
        try:
            return await self.authorized_users_col.count_documents({})
        except Exception as e:
            logger.error(f"Error in get_authorized_users_count: {e}", exc_info=True)
            return 0

    async def get_regular_users_count(self) -> int:
        try:
            auth_ids = await self.authorized_users_col.distinct("user_id")
            return await self.col.count_documents({"id": {"$nin": auth_ids}})
        except Exception as e:
            logger.error(f"Error in get_regular_users_count: {e}", exc_info=True)
            return 0

    async def get_all_users(self):
        # find() only builds a cursor server-side; it cannot fail here
        return self.col.find({})

    async def get_authorized_users_cursor(self):
        return self.authorized_users_col.find({})

    async def get_regular_users_cursor(self):
        auth_ids = await self.authorized_users_col.distinct("user_id")
        return self.col.find({"id": {"$nin": auth_ids}})

    async def delete_user(self, user_id: int):
        try:
            await self.col.delete_one({"id": user_id})
            logger.debug(f"Deleted user {user_id}.")
        except Exception as e:
            logger.error(f"Error in delete_user for user {user_id}: {e}", exc_info=True)
            raise

    async def add_banned_user(
        self, user_id: int, banned_by: int | None = None, reason: str | None = None
    ):
        try:
            ban_data = {
                "user_id": user_id,
                "banned_at": datetime.datetime.now(datetime.UTC),
                "banned_by": banned_by,
                "reason": reason,
            }
            await self.banned_users_col.update_one(
                {"user_id": user_id}, {"$set": ban_data}, upsert=True
            )
            # the ban gate is flag-cached (H7): without this invalidation a
            # fresh ban would not take effect until the 5-min TTL expired
            flags.invalidate(("banned_user", user_id))
            logger.debug(f"Added/Updated banned user {user_id}. Reason: {reason}")
        except Exception as e:
            logger.error(f"Error in add_banned_user for user {user_id}: {e}", exc_info=True)
            raise

    async def remove_banned_user(self, user_id: int) -> bool:
        try:
            result = await self.banned_users_col.delete_one({"user_id": user_id})
            # invalidate unconditionally (a miss is a no-op): a stale cached
            # ban entry would keep a re-banned user denied until TTL expiry
            flags.invalidate(("banned_user", user_id))
            if result.deleted_count > 0:
                logger.debug(f"Removed banned user {user_id}.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error in remove_banned_user for user {user_id}: {e}", exc_info=True)
            return False

    async def is_user_banned(
        self, user_id: int, *, raise_on_error: bool = False
    ) -> dict[str, Any] | None:
        """Fetch a ban record.  With ``raise_on_error=True`` a Mongo failure
        raises so fail-closed callers (the ban gate, H7) can deny instead of
        silently treating the outage as "not banned"."""
        try:
            return await self.banned_users_col.find_one({"user_id": user_id})
        except Exception as e:
            if raise_on_error:
                raise
            logger.error(f"Error in is_user_banned for user {user_id}: {e}", exc_info=True)
            return None

    async def add_banned_channel(
        self, channel_id: int, banned_by: int | None = None, reason: str | None = None
    ):
        try:
            ban_data = {
                "channel_id": channel_id,
                "banned_at": datetime.datetime.now(datetime.UTC),
                "banned_by": banned_by,
                "reason": reason,
            }
            await self.banned_channels_col.update_one(
                {"channel_id": channel_id}, {"$set": ban_data}, upsert=True
            )
            flags.invalidate(("banned_channel", channel_id))
            logger.debug(f"Added/Updated banned channel {channel_id}. Reason: {reason}")
        except Exception as e:
            logger.error(
                f"Error in add_banned_channel for channel {channel_id}: {e}", exc_info=True
            )
            raise

    async def remove_banned_channel(self, channel_id: int) -> bool:
        try:
            result = await self.banned_channels_col.delete_one({"channel_id": channel_id})
            flags.invalidate(("banned_channel", channel_id))
            if result.deleted_count > 0:
                logger.debug(f"Removed banned channel {channel_id}.")
                return True
            return False
        except Exception as e:
            logger.error(
                f"Error in remove_banned_channel for channel {channel_id}: {e}", exc_info=True
            )
            return False

    async def is_channel_banned(
        self, channel_id: int, *, raise_on_error: bool = False
    ) -> dict[str, Any] | None:
        """Fetch a channel-ban record.  ``raise_on_error`` mirrors
        ``is_user_banned`` for fail-closed callers; the default (swallow →
        None) is what the auto-leave gate wants, since a Mongo outage must
        not trigger the destructive leave_chat action."""
        try:
            return await self.banned_channels_col.find_one({"channel_id": channel_id})
        except Exception as e:
            if raise_on_error:
                raise
            logger.error(f"Error in is_channel_banned for channel {channel_id}: {e}", exc_info=True)
            return None

    async def save_main_token(
        self,
        user_id: int,
        token_value: str,
        expires_at: datetime.datetime,
        created_at: datetime.datetime,
        activated: bool,
    ) -> None:
        try:
            await self.token_col.update_one(
                {"user_id": user_id, "token": token_value},
                {
                    "$set": {
                        "expires_at": expires_at,
                        "created_at": created_at,
                        "activated": activated,
                    }
                },
                upsert=True,
            )
            logger.debug(
                f"Saved main token {token_value} for user {user_id} with activated status {activated}."
            )
        except Exception as e:
            logger.error(f"Error saving main token for user {user_id}: {e}", exc_info=True)
            raise

    async def add_restart_message(self, message_id: int, chat_id: int) -> None:
        try:
            await self.restart_message_col.insert_one(
                {
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "timestamp": datetime.datetime.now(datetime.UTC),
                }
            )
            logger.debug(f"Added restart message {message_id} for chat {chat_id}.")
        except Exception as e:
            logger.error(f"Error adding restart message {message_id}: {e}", exc_info=True)

    async def get_restart_message(self) -> dict[str, Any] | None:
        try:
            return await self.restart_message_col.find_one(sort=[("timestamp", -1)])
        except Exception as e:
            logger.error(f"Error getting restart message: {e}", exc_info=True)
            return None

    async def delete_restart_message(self, message_id: int) -> None:
        try:
            await self.restart_message_col.delete_one({"message_id": message_id})
            logger.debug(f"Deleted restart message {message_id}.")
        except Exception as e:
            logger.error(f"Error deleting restart message {message_id}: {e}", exc_info=True)

    async def is_user_authorized(self, user_id: int) -> bool:
        try:
            user = await self.authorized_users_col.find_one({"user_id": user_id}, {"_id": 1})
            return bool(user)
        except Exception as e:
            logger.error(f"Error in is_user_authorized for user {user_id}: {e}", exc_info=True)
            return False

    async def get_file_by_unique_id(self, file_unique_id: str) -> dict[str, Any] | None:
        try:
            return await self.files_col.find_one({"file_unique_id": file_unique_id})
        except Exception as e:
            logger.error(f"Error getting file by unique_id {file_unique_id}: {e}", exc_info=True)
            return None

    async def get_file_by_hash(
        self, public_hash: str, *, raise_on_error: bool = True
    ) -> dict[str, Any] | None:
        try:
            return await self.files_col.find_one({"public_hash": public_hash})
        except Exception as e:
            logger.error(f"Error getting file by hash {public_hash}: {e}", exc_info=True)
            if raise_on_error:
                raise
            return None

    async def create_file_record(self, file_record: dict[str, Any]) -> None:
        try:
            await self.files_col.insert_one(file_record)
        except Exception as e:
            logger.error(
                f"Error creating canonical file record for {file_record.get('file_unique_id')}: {e}",
                exc_info=True,
            )
            raise

    async def replace_file_record(self, file_record: dict[str, Any]) -> None:
        try:
            await self.files_col.replace_one(
                {"file_unique_id": file_record["file_unique_id"]}, file_record, upsert=True
            )
        except Exception as e:
            logger.error(
                f"Error replacing canonical file record for {file_record.get('file_unique_id')}: {e}",
                exc_info=True,
            )
            raise

    async def bulk_touch_file_records(
        self, items: list[tuple[str, int, int]], *, raise_on_error: bool = False
    ) -> bool:
        """Batched touch (M14): one BulkWrite for the whole flush cycle.

        ``items`` is a list of ``(public_hash, reuse_delta, seen_delta)``
        triples; deltas accumulate per hash so N touches flush as N, not 1.
        """
        if not items:
            return True
        now = datetime.datetime.now(datetime.UTC)
        ops: list[UpdateOne] = []
        for public_hash, reuse_delta, seen_delta in items:
            inc: dict[str, int] = {"seen_count": seen_delta}
            if reuse_delta:
                inc["reuse_count"] = reuse_delta
            ops.append(
                UpdateOne(
                    {"public_hash": public_hash},
                    {"$set": {"last_seen_at": now}, "$inc": inc},
                )
            )
        try:
            await self.files_col.bulk_write(ops, ordered=False)
            return True
        except Exception as e:
            logger.error(f"Error bulk-touching {len(ops)} file records: {e}", exc_info=True)
            if raise_on_error:
                raise
            return False

    async def delete_file_record(self, public_hash: str) -> bool:
        """Remove a stale canonical record (M10 self-healing)."""
        try:
            result = await self.files_col.delete_one({"public_hash": public_hash})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting stale file record {public_hash}: {e}", exc_info=True)
            return False

    async def update_file_id(
        self, public_hash: str, file_id: str, *, raise_on_error: bool = False
    ) -> bool:
        try:
            await self.files_col.update_one(
                {"public_hash": public_hash},
                {"$set": {"file_id": file_id, "last_seen_at": datetime.datetime.now(datetime.UTC)}},
            )
            return True
        except Exception as e:
            logger.error(f"Error updating file_id for {public_hash}: {e}", exc_info=True)
            if raise_on_error:
                raise
            return False

    async def acquire_file_ingest_claim(
        self, file_unique_id: str, *, ttl_seconds: int = 60
    ) -> str | None:
        """Acquire the ingest claim; returns an opaque owner token, or
        ``None`` when another worker holds a live claim.

        The owner token makes the matching ``release_file_ingest_claim``
        refuse to delete a newer worker's claim after this worker's TTL
        expired mid-copy (which previously caused a redundant third copy).
        """
        now = datetime.datetime.now(datetime.UTC)
        owner = uuid.uuid4().hex
        claim_fields = {
            "owner": owner,
            "created_at": now,
            "expires_at": now + datetime.timedelta(seconds=ttl_seconds),
        }
        try:
            await self.file_ingest_locks_col.insert_one({"_id": file_unique_id, **claim_fields})
            return owner
        except DuplicateKeyError:
            try:
                result = await self.file_ingest_locks_col.find_one_and_update(
                    {
                        "_id": file_unique_id,
                        "$or": [{"expires_at": {"$lte": now}}, {"expires_at": {"$exists": False}}],
                    },
                    {"$set": claim_fields},
                    return_document=False,
                )
                return owner if result else None
            except Exception as e:
                logger.error(
                    f"Error updating ingest claim for {file_unique_id}: {e}", exc_info=True
                )
                raise
        except Exception as e:
            logger.error(f"Error acquiring ingest claim for {file_unique_id}: {e}", exc_info=True)
            raise

    async def release_file_ingest_claim(self, file_unique_id: str, owner: str) -> bool:
        """Release the claim only if we still own it (owner-checked)."""
        try:
            result = await self.file_ingest_locks_col.delete_one(
                {"_id": file_unique_id, "owner": owner}
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error releasing ingest claim for {file_unique_id}: {e}", exc_info=True)
            return False

    async def is_file_ingest_claim_active(self, file_unique_id: str) -> bool:
        try:
            claim = await self.file_ingest_locks_col.find_one(
                {"_id": file_unique_id, "expires_at": {"$gt": datetime.datetime.now(datetime.UTC)}},
                {"_id": 1},
            )
            return bool(claim)
        except Exception as e:
            logger.error(f"Error checking ingest claim for {file_unique_id}: {e}", exc_info=True)
            raise

    async def close(self):
        if self._client:
            await self._client.close()


db = Database(Var.DATABASE_URL, Var.NAME)
