from collections import OrderedDict

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from Thunder.utils.logger import logger

from .ban_repo import _BanRepo
from .file_repo import _FileRepo
from .lock_repo import _LockRepo
from .restart_repo import _RestartRepo
from .token_repo import _TokenRepo
from .user_repo import _UserRepo


class Database(_UserRepo, _BanRepo, _FileRepo, _TokenRepo, _LockRepo, _RestartRepo):
    _USER_CACHE_TTL = 300  # 5 minutes

    def __init__(self, uri: str, database_name: str, *args, **kwargs):
        self._client = AsyncMongoClient(
            uri,
            maxPoolSize=50,
            minPoolSize=5,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            retryWrites=True,
            *args, **kwargs
        )
        self.db = self._client[database_name]
        self.col: AsyncCollection = self.db.users
        self.banned_users_col: AsyncCollection = self.db.banned_users
        self.banned_channels_col: AsyncCollection = self.db.banned_channels
        self.token_col: AsyncCollection = self.db.tokens
        self.authorized_users_col: AsyncCollection = self.db.authorized_users
        self.restart_message_col: AsyncCollection = self.db.restart_message
        self.files_col: AsyncCollection = self.db.files
        self.file_ingest_locks_col: AsyncCollection = self.db.file_ingest_locks
        self._user_exist_cache: OrderedDict[int, tuple[bool, float]] = OrderedDict()
        self._banned_cache: OrderedDict[int, tuple[dict | None, float]] = OrderedDict()
        self._channel_ban_cache: OrderedDict[int, tuple[dict | None, float]] = OrderedDict()

    async def ensure_indexes(self, *, raise_on_error: bool = True) -> bool:
        try:
            await self.banned_users_col.create_index("user_id", unique=True)
            await self.banned_channels_col.create_index("channel_id", unique=True)
            await self.token_col.create_index("token", unique=True)
            await self.authorized_users_col.create_index("user_id", unique=True)
            try:
                await self.col.create_index("id", unique=True)
            except DuplicateKeyError:
                logger.warning("Duplicate users found, deduplicating...")
                await self._deduplicate_users()
                await self.col.create_index("id", unique=True)
            await self.token_col.create_index("expires_at", expireAfterSeconds=0)
            await self.token_col.create_index("activated")
            await self.restart_message_col.create_index("message_id", unique=True)
            await self.restart_message_col.create_index("timestamp", expireAfterSeconds=3600)
            await self.files_col.create_index("file_unique_id", unique=True)
            await self.files_col.create_index("public_hash", unique=True)
            await self.files_col.create_index("canonical_message_id", unique=True)
            await self.files_col.create_index("created_at")
            await self.files_col.create_index("last_seen_at")
            await self.file_ingest_locks_col.create_index("expires_at", expireAfterSeconds=0)

            logger.debug("Database indexes ensured.")
            return True
        except Exception as e:
            logger.error(f"Error in ensure_indexes: {e}", exc_info=True)
            if raise_on_error:
                raise
            return False

    async def close(self):
        if self._client:
            await self._client.close()
