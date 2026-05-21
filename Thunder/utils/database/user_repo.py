import datetime
import time
from collections import OrderedDict

from Thunder.utils.logger import logger


class _UserRepo:
    _USER_CACHE_TTL: int
    col: object
    authorized_users_col: object
    _user_exist_cache: OrderedDict

    def _new_user(self, user_id: int) -> dict:
        try:
            return {
                'id': user_id,
                'join_date': datetime.datetime.now(datetime.UTC)
            }
        except Exception as e:
            logger.error(f"Error in new_user for user {user_id}: {e}", exc_info=True)
            raise

    async def add_user(self, user_id: int) -> bool:
        try:
            result = await self.col.update_one(
                {'id': user_id},
                {'$setOnInsert': self._new_user(user_id)},
                upsert=True
            )
            if result.upserted_id:
                self._user_exist_cache.pop(user_id, None)
                logger.debug(f"Added new user {user_id} to database.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error in add_user for user {user_id}: {e}", exc_info=True)
            raise

    async def is_user_exist(self, user_id: int) -> bool:
        """Read-only existence check. For user registration, use add_user() instead."""
        now = time.time()
        if user_id in self._user_exist_cache:
            result, ts = self._user_exist_cache[user_id]
            if now - ts < self._USER_CACHE_TTL:
                self._user_exist_cache.move_to_end(user_id)
                return result
        try:
            user = await self.col.find_one({'id': user_id}, {'_id': 1})
            result = bool(user)
            self._user_exist_cache[user_id] = (result, now)
            if len(self._user_exist_cache) > 5000:
                self._user_exist_cache.popitem(last=False)
            return result
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
        try:
            return self.col.find({})
        except Exception as e:
            logger.error(f"Error in get_all_users: {e}", exc_info=True)
            return self.col.find({"_id": {"$exists": False}})

    async def get_authorized_users_cursor(self):
        try:
            return self.authorized_users_col.find({})
        except Exception as e:
            logger.error(f"Error in get_authorized_users_cursor: {e}", exc_info=True)
            return self.authorized_users_col.find({"_id": {"$exists": False}})

    async def get_regular_users_cursor(self):
        try:
            auth_ids = await self.authorized_users_col.distinct("user_id")
            return self.col.find({"id": {"$nin": auth_ids}})
        except Exception as e:
            logger.error(f"Error in get_regular_users_cursor: {e}", exc_info=True)
            return self.col.find({"_id": {"$exists": False}})

    async def delete_user(self, user_id: int):
        try:
            await self.col.delete_one({'id': user_id})
            logger.debug(f"Deleted user {user_id}.")
        except Exception as e:
            logger.error(f"Error in delete_user for user {user_id}: {e}", exc_info=True)
            raise

    async def _deduplicate_users(self) -> None:
        pipeline = [
            {"$sort": {"join_date": 1}},
            {"$group": {"_id": "$id", "doc_id": {"$first": "$_id"}}},
            {"$project": {"_id": "$doc_id"}}
        ]
        keep_ids = []
        async for doc in self.col.aggregate(pipeline):
            keep_ids.append(doc["_id"])
        if keep_ids:
            result = await self.col.delete_many({"_id": {"$nin": keep_ids}})
            if result.deleted_count > 0:
                logger.warning(f"Deduplicated {result.deleted_count} duplicate user documents.")
