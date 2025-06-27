# Thunder/utils/database.py

import datetime
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from Thunder.vars import Var
from Thunder.utils.logger import logger

class Database:
    def __init__(self, uri: str, database_name: str, *args, **kwargs):
        self._client = AsyncIOMotorClient(uri, *args, **kwargs)
        self.db = self._client[database_name]
        self.col: AsyncIOMotorCollection = self.db.users
        self.banned_users_col: AsyncIOMotorCollection = self.db.banned_users
        self.token_col: AsyncIOMotorCollection = self.db.tokens
        self.authorized_users_col: AsyncIOMotorCollection = self.db.authorized_users
        self.restart_message_col: AsyncIOMotorCollection = self.db.restart_message

    async def ensure_indexes(self):
        try:
            await self.banned_users_col.create_index("user_id", unique=True)
            await self.token_col.create_index("token", unique=True)
            await self.authorized_users_col.create_index("user_id", unique=True)
            await self.col.create_index("id", unique=True)
            await self.token_col.create_index("expires_at", expireAfterSeconds=0)
            await self.token_col.create_index("activated")
            await self.restart_message_col.create_index("message_id", unique=True)
            await self.restart_message_col.create_index("timestamp", expireAfterSeconds=3600)

            logger.debug("Database indexes ensured.")
        except Exception as e:
            logger.error(f"Error in ensure_indexes: {e}", exc_info=True)
            raise

    def new_user(self, user_id: int) -> dict:
        try:
            return {
                'id': user_id,
                'join_date': datetime.datetime.utcnow()
            }
        except Exception as e:
            logger.error(f"Error in new_user for user {user_id}: {e}", exc_info=True)
            raise

    async def add_user(self, user_id: int):
        try:
            if not await self.is_user_exist(user_id):
                await self.col.insert_one(self.new_user(user_id))
                logger.debug(f"Added new user {user_id} to database.")
        except Exception as e:
            logger.error(f"Error in add_user for user {user_id}: {e}", exc_info=True)
            raise


    async def is_user_exist(self, user_id: int) -> bool:
        try:
            user = await self.col.find_one({'id': user_id}, {'_id': 1})
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

    async def get_all_users(self):
        try:
            return self.col.find({})
        except Exception as e:
            logger.error(f"Error in get_all_users: {e}", exc_info=True)
            return []

    async def delete_user(self, user_id: int):
        try:
            await self.col.delete_one({'id': user_id})
            logger.debug(f"Deleted user {user_id}.")
        except Exception as e:
            logger.error(f"Error in delete_user for user {user_id}: {e}", exc_info=True)
            raise


    async def add_banned_user(
        self, user_id: int, banned_by: Optional[int] = None,
        reason: Optional[str] = None, ban_time: Optional[str] = None
    ):
        try:
            ban_data = {
                "user_id": user_id,
                "banned_at": datetime.datetime.utcnow(),
                "banned_by": banned_by,
                "reason": reason
            }
            await self.banned_users_col.update_one(
                {"user_id": user_id},
                {"$set": ban_data},
                upsert=True
            )
            logger.debug(f"Added/Updated banned user {user_id}. Reason: {reason}")
        except Exception as e:
            logger.error(f"Error in add_banned_user for user {user_id}: {e}", exc_info=True)
            raise

    async def remove_banned_user(self, user_id: int) -> bool:
        try:
            result = await self.banned_users_col.delete_one({"user_id": user_id})
            if result.deleted_count > 0:
                logger.debug(f"Removed banned user {user_id}.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error in remove_banned_user for user {user_id}: {e}", exc_info=True)
            return False

    async def is_user_banned(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            return await self.banned_users_col.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"Error in is_user_banned for user {user_id}: {e}", exc_info=True)
            return None

    async def save_main_token(self, user_id: int, token_value: str, expires_at: datetime.datetime, created_at: datetime.datetime, activated: bool) -> None:
        try:
            await self.token_col.update_one(
                {"user_id": user_id, "token": token_value},
                {"$set": {
                    "expires_at": expires_at,
                    "created_at": created_at,
                    "activated": activated
                    }
                },
                upsert=True
            )
            logger.debug(f"Saved main token {token_value} for user {user_id} with activated status {activated}.")
        except Exception as e:
            logger.error(f"Error saving main token for user {user_id}: {e}", exc_info=True)
            raise


    async def add_restart_message(self, message_id: int, chat_id: int) -> None:
        try:
            await self.restart_message_col.insert_one({
                "message_id": message_id,
                "chat_id": chat_id,
                "timestamp": datetime.datetime.utcnow()
            })
            logger.debug(f"Added restart message {message_id} for chat {chat_id}.")
        except Exception as e:
            logger.error(f"Error adding restart message {message_id}: {e}", exc_info=True)

    async def get_restart_message(self) -> Optional[Dict[str, Any]]:
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

    async def close(self):
        if self._client:
            self._client.close()

db = Database(Var.DATABASE_URL, Var.NAME)
