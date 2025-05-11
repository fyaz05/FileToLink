# Thunder/utils/database.py

import datetime
from typing import Optional, List, Dict, Any
import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorCollection
from Thunder.utils.logger import logger

class Database:
    # Database class for handling user data using MongoDB
    
    def __init__(self, uri: str, database_name: str):
        # Initialize database connection
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col: AsyncIOMotorCollection = self.db.users
        self.banned_users_col: AsyncIOMotorCollection = self.db.banned_users
    
    async def ensure_indexes(self):
        # Ensure necessary indexes are created
        await self.banned_users_col.create_index("user_id", unique=True)
        logger.info(
            "Database indexes ensured for users and banned_users collections."
        )

    def new_user(self, user_id: int) -> dict:
        # Create new user document
        return {
            'id': user_id,
            'join_date': datetime.datetime.utcnow()
        }
    
    async def add_user(self, user_id: int):
        # Add new user if not exists
        if not await self.is_user_exist(user_id):
            user = self.new_user(user_id)
            await self.col.insert_one(user)
    
    async def add_user_pass(self, user_id: int, ag_pass: str):
        # Add or update user password
        await self.add_user(user_id)
        await self.col.update_one({'id': user_id}, {'$set': {'ag_p': ag_pass}})
    
    async def get_user_pass(self, user_id: int) -> Optional[str]:
        # Get user password
        user_pass = await self.col.find_one({'id': user_id}, {'ag_p': 1})
        return user_pass.get('ag_p') if user_pass else None
    
    async def is_user_exist(self, user_id: int) -> bool:
        # Check if user exists
        user = await self.col.find_one({'id': user_id}, {'_id': 1})
        return bool(user)
    
    async def total_users_count(self) -> int:
        # Count total users
        return await self.col.count_documents({})
    
    async def get_all_users(self):
        # Get all users (returns cursor)
        return self.col.find({})
    
    async def delete_user(self, user_id: int):
        # Delete user
        await self.col.delete_one({'id': user_id})
    
    async def create_index(self):
        # Create index for faster queries
        await self.col.create_index("id", unique=True)
        
    async def get_active_users(self, days: int = 7):
        # Get users active within specified days
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        return self.col.find({'join_date': {'$gte': cutoff}})
        
    async def add_banned_user(
        self, user_id: int, banned_by: Optional[int] = None, 
        reason: Optional[str] = None
    ):
        # Add or update banned user with upsert
        ban_data = {
            "user_id": user_id,
            "banned_at": datetime.datetime.utcnow(),
            "banned_by": banned_by,
            "reason": reason
        }
        try:
            await self.banned_users_col.update_one(
                {"user_id": user_id},
                {"$set": ban_data},
                upsert=True
            )
        except Exception as e:
            logger.error(
                f"Database error in add_banned_user for user {user_id}: {e}"
            )
            # MCP best practice: raise with clear error
            raise RuntimeError(f"Failed to ban user {user_id}: {e}")

    async def remove_banned_user(self, user_id: int) -> bool:
        # Remove banned user and return if document was deleted
        try:
            result = await self.banned_users_col.delete_one(
                {"user_id": user_id}
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.error(
                f"Database error in remove_banned_user for user {user_id}: {e}"
            )
            # MCP best practice: return False on error
            return False

    async def is_user_banned(self, user_id: int) -> Optional[Dict[str, Any]]:
        # Check if user is banned and return ban details
        try:
            return await self.banned_users_col.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(
                f"Database error in is_user_banned for user {user_id}: {e}"
            )
            # MCP best practice: return None on error
            return None

    async def close(self):
        # Close database connection
        if self._client:
            self._client.close()
