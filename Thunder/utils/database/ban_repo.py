import datetime
import time
from collections import OrderedDict
from typing import Any

from Thunder.utils.logger import logger


class _BanRepo:
    banned_users_col: object
    banned_channels_col: object
    _banned_cache: OrderedDict
    _channel_ban_cache: OrderedDict
    _USER_CACHE_TTL: int

    async def add_banned_user(
        self, user_id: int, banned_by: int | None = None,
        reason: str | None = None
    ):
        try:
            ban_data = {
                "user_id": user_id,
                "banned_at": datetime.datetime.now(datetime.UTC),
                "banned_by": banned_by,
                "reason": reason
            }
            await self.banned_users_col.update_one(
                {"user_id": user_id},
                {"$set": ban_data},
                upsert=True
            )
            self._banned_cache.pop(user_id, None)
            logger.debug(f"Added/Updated banned user {user_id}. Reason: {reason}")
        except Exception as e:
            logger.error(f"Error in add_banned_user for user {user_id}: {e}", exc_info=True)
            raise

    async def remove_banned_user(self, user_id: int) -> bool:
        try:
            result = await self.banned_users_col.delete_one({"user_id": user_id})
            if result.deleted_count > 0:
                self._banned_cache.pop(user_id, None)
                logger.debug(f"Removed banned user {user_id}.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error in remove_banned_user for user {user_id}: {e}", exc_info=True)
            return False

    async def is_user_banned(self, user_id: int) -> dict[str, Any] | None:
        now = time.time()
        if user_id in self._banned_cache:
            cached_doc, ts = self._banned_cache[user_id]
            if now - ts < self._USER_CACHE_TTL:
                self._banned_cache.move_to_end(user_id)
                return cached_doc
        try:
            result = await self.banned_users_col.find_one({"user_id": user_id})
            self._banned_cache[user_id] = (result, now)
            if len(self._banned_cache) > 5000:
                self._banned_cache.popitem(last=False)
            return result
        except Exception as e:
            logger.error(f"Error in is_user_banned for user {user_id}: {e}", exc_info=True)
            return None

    async def add_banned_channel(
        self, channel_id: int, banned_by: int | None = None,
        reason: str | None = None
    ):
        try:
            ban_data = {
                "channel_id": channel_id,
                "banned_at": datetime.datetime.now(datetime.UTC),
                "banned_by": banned_by,
                "reason": reason
            }
            await self.banned_channels_col.update_one(
                {"channel_id": channel_id},
                {"$set": ban_data},
                upsert=True
            )
            self._channel_ban_cache.pop(channel_id, None)
            logger.debug(f"Added/Updated banned channel {channel_id}. Reason: {reason}")
        except Exception as e:
            logger.error(f"Error in add_banned_channel for channel {channel_id}: {e}", exc_info=True)
            raise

    async def remove_banned_channel(self, channel_id: int) -> bool:
        try:
            result = await self.banned_channels_col.delete_one({"channel_id": channel_id})
            if result.deleted_count > 0:
                self._channel_ban_cache.pop(channel_id, None)
                logger.debug(f"Removed banned channel {channel_id}.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error in remove_banned_channel for channel {channel_id}: {e}", exc_info=True)
            return False

    async def is_channel_banned(self, channel_id: int) -> dict[str, Any] | None:
        now = time.time()
        if channel_id in self._channel_ban_cache:
            cached_doc, ts = self._channel_ban_cache[channel_id]
            if now - ts < self._USER_CACHE_TTL:
                self._channel_ban_cache.move_to_end(channel_id)
                return cached_doc
        try:
            result = await self.banned_channels_col.find_one({"channel_id": channel_id})
            self._channel_ban_cache[channel_id] = (result, now)
            if len(self._channel_ban_cache) > 5000:
                self._channel_ban_cache.popitem(last=False)
            return result
        except Exception as e:
            logger.error(f"Error in is_channel_banned for channel {channel_id}: {e}", exc_info=True)
            return None
