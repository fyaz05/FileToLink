# Thunder/utils/database.py

import datetime
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from Thunder.vars import Var
from Thunder.utils.logger import logger
from Thunder.utils.error_handling import log_errors

class Database:
    def __init__(self, uri: str, database_name: str, *args, **kwargs):
        self._client = AsyncIOMotorClient(uri, *args, **kwargs)
        self.db = self._client[database_name]
        self.col: AsyncIOMotorCollection = self.db.users
        self.banned_users_col: AsyncIOMotorCollection = self.db.banned_users
        self.token_col: AsyncIOMotorCollection = self.db.tokens
        self.authorized_users_col: AsyncIOMotorCollection = self.db.authorized_users
        self.restart_message_col: AsyncIOMotorCollection = self.db.restart_message

    @log_errors
    async def ensure_indexes(self):
        await self.banned_users_col.create_index("user_id", unique=True)
        await self.token_col.create_index("token", unique=True)
        await self.authorized_users_col.create_index("user_id", unique=True)
        await self.col.create_index("id", unique=True)
        await self.token_col.create_index("expires_at", expireAfterSeconds=0)
        await self.restart_message_col.create_index("message_id", unique=True)
        await self.restart_message_col.create_index("timestamp", expireAfterSeconds=3600)
        logger.info("Database indexes ensured.")

    @log_errors
    def new_user(self, user_id: int) -> dict:
        return {
            'id': user_id,
            'join_date': datetime.datetime.utcnow()
        }

    @log_errors
    async def add_user(self, user_id: int):
        if not await self.is_user_exist(user_id):
            await self.col.insert_one(self.new_user(user_id))
            logger.debug(f"Added new user {user_id} to database.")

    @log_errors
    async def add_user_pass(self, user_id: int, ag_pass: str):
        await self.add_user(user_id)
        await self.col.update_one({'id': user_id}, {'$set': {'ag_p': ag_pass}})
        logger.debug(f"Updated password for user {user_id}.")

    @log_errors
    async def get_user_pass(self, user_id: int) -> Optional[str]:
        user_data = await self.col.find_one({'id': user_id}, {'ag_p': 1})
        return user_data.get('ag_p') if user_data else None

    @log_errors
    async def is_user_exist(self, user_id: int) -> bool:
        user = await self.col.find_one({'id': user_id}, {'_id': 1})
        return bool(user)

    @log_errors
    async def total_users_count(self) -> int:
        return await self.col.count_documents({})

    @log_errors
    async def get_all_users(self):
        return self.col.find({})

    @log_errors
    async def delete_user(self, user_id: int):
        await self.col.delete_one({'id': user_id})
        logger.info(f"Deleted user {user_id}.")

    @log_errors
    async def create_index(self):
        await self.col.create_index("id", unique=True)
        logger.info("Created index for 'id' on users collection.")

    @log_errors
    async def get_active_users(self, days: int = 7):
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        return self.col.find({'join_date': {'$gte': cutoff}})

    @log_errors
    async def add_banned_user(
        self, user_id: int, banned_by: Optional[int] = None,
        reason: Optional[str] = None, ban_time: Optional[str] = None
    ):
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
        logger.info(f"Added/Updated banned user {user_id}. Reason: {reason}")

    @log_errors
    async def remove_banned_user(self, user_id: int) -> bool:
        result = await self.banned_users_col.delete_one({"user_id": user_id})
        if result.deleted_count > 0:
            logger.info(f"Removed banned user {user_id}.")
            return True
        return False

    @log_errors
    async def is_user_banned(self, user_id: int) -> Optional[Dict[str, Any]]:
        return await self.banned_users_col.find_one({"user_id": user_id})

    @log_errors
    async def check_user_token(self, user_id: int) -> Dict[str, bool]:
        token_data = await self.token_col.find_one({
            "user_id": user_id,
            "expires_at": {"$gt": datetime.datetime.utcnow()}
        })
        return {"has_token": bool(token_data)}

    @log_errors
    async def update_user_token(self, user_id: int, token: str) -> None:
        token_data = await self.token_col.find_one({"token": token})
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=Var.TOKEN_TTL_HOURS)
        if token_data:
            await self.token_col.update_one(
                {"token": token},
                {"$set": {"user_id": user_id, "expires_at": expires_at}}
            )
            logger.info(f"Updated token for user {user_id}.")
        else:
            await self.token_col.insert_one({
                "user_id": user_id,
                "token": token,
                "created_at": datetime.datetime.utcnow(),
                "expires_at": expires_at
            })
            logger.info(f"Inserted new token for user {user_id}.")

    @log_errors
    async def save_broadcast_state(self, broadcast_id, state_data):
        await self.db.broadcasts.update_one(
            {"_id": broadcast_id},
            {"$set": state_data},
            upsert=True
        )
        logger.debug(f"Saved broadcast state for ID {broadcast_id}.")

    @log_errors
    async def get_broadcast_state(self, broadcast_id):
        return await self.db.broadcasts.find_one({"_id": broadcast_id})

    @log_errors
    async def list_active_broadcasts(self):
        cursor = self.db.broadcasts.find({"is_cancelled": False})
        return await cursor.to_list(length=None)

    @log_errors
    async def add_restart_message(self, message_id: int, chat_id: int) -> None:
        try:
            await self.restart_message_col.insert_one({
                "message_id": message_id, 
                "chat_id": chat_id, 
                "timestamp": datetime.datetime.utcnow()
            })
            logger.info(f"Added restart message {message_id} for chat {chat_id}.")
        except Exception as e:
            logger.error(f"Error adding restart message {message_id}: {e}")

    @log_errors
    async def get_restart_message(self) -> Optional[Dict[str, Any]]:
        try:
            return await self.restart_message_col.find_one(sort=[("timestamp", -1)])
        except Exception as e:
            logger.error(f"Error getting restart message: {e}")
            return None

    @log_errors
    async def delete_restart_message(self, message_id: int) -> None:
        try:
            await self.restart_message_col.delete_one({"message_id": message_id})
            logger.info(f"Deleted restart message {message_id}.")
        except Exception as e:
            logger.error(f"Error deleting restart message {message_id}: {e}")

    async def close(self):
        if self._client:
            self._client.close()

db = Database(Var.DATABASE_URL, Var.NAME)
