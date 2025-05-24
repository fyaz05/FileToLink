"""Database utilities for managing users, bans, and tokens via MongoDB."""
import datetime
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection
from Thunder.utils.logger import logger
from Thunder.vars import Var

class Database:
    # Database class for handling user data using MongoDB
    
    def __init__(self, uri: str, database_name: str, *args, **kwargs):
        # Initialize database connection
        self._client = AsyncIOMotorClient(uri, *args, **kwargs)
        self.db = self._client[database_name]
        self.col: AsyncIOMotorCollection = self.db.users
        self.banned_users_col: AsyncIOMotorCollection = self.db.banned_users
        self.token_col: AsyncIOMotorCollection = self.db.tokens
        self.authorized_users_col: AsyncIOMotorCollection = self.db.authorized_users
        self.restart_message_col: AsyncIOMotorCollection = self.db.restart_message
    
    async def ensure_indexes(self):
        # Ensure necessary indexes are created
        await self.banned_users_col.create_index("user_id", unique=True)
        await self.token_col.create_index("token", unique=True)
        await self.authorized_users_col.create_index("user_id", unique=True)
        # Add unique index on users.id
        await self.col.create_index("id", unique=True)

        # TTL index to auto-remove expired tokens
        await self.token_col.create_index("expires_at", expireAfterSeconds=0)
        await self.restart_message_col.create_index("message_id", unique=True)
        # TTL index to auto-remove expired restart messages
        await self.restart_message_col.create_index("timestamp", expireAfterSeconds=3600)

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
        reason: Optional[str] = None, ban_time: Optional[str] = None
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
            return False

    async def is_user_banned(self, user_id: int) -> Optional[Dict[str, Any]]:
        # Check if user is banned and return ban details
        try:
            return await self.banned_users_col.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(
                f"Database error in is_user_banned for user {user_id}: {e}"
            )
            return None

    async def check_user_token(self, user_id: int) -> Dict[str, bool]:
        """Check if user has valid token."""
        try:
            # Only return active (not expired) tokens
            token_data = await self.token_col.find_one({
                "user_id": user_id,
                "expires_at": {"$gt": datetime.datetime.utcnow()}
            })
            return {"has_token": bool(token_data)}
        except Exception as e:
            logger.error(f"Database error checking token for {user_id}: {e}")
            return {"has_token": False}
            
    async def update_user_token(self, user_id: int, token: str) -> None:
        """Update user's active token in database."""
        try:
            # Find the token data first
            token_data = await self.token_col.find_one({"token": token})
            
            if token_data:
                # Link token to user and refresh expiration
                await self.token_col.update_one(
                    {"token": token},
                    {"$set": {
                        "user_id": user_id,
                        "expires_at": datetime.datetime.utcnow() + datetime.timedelta(hours=Var.TOKEN_TTL_HOURS)
                    }}
                )
            else:
                # If no token data found, create a basic entry (this is a fallback)
                logger.warning(f"Token {token} not found in database when assigning to user {user_id}")
                await self.token_col.insert_one({
                    "user_id": user_id,
                    "token": token,
                    "created_at": datetime.datetime.utcnow(),
                    "expires_at": datetime.datetime.utcnow() + datetime.timedelta(hours=Var.TOKEN_TTL_HOURS)
                })
        except Exception as e:
            logger.error(f"Database error updating token for {user_id}: {e}")
            raise RuntimeError(f"Failed to update token: {e}")

    async def save_broadcast_state(self, broadcast_id, state_data):
        broadcast_collection = self.db.broadcasts
        await broadcast_collection.update_one(
            {"_id": broadcast_id},
            {"$set": state_data},
            upsert=True
        )

    async def get_broadcast_state(self, broadcast_id):
        broadcast_collection = self.db.broadcasts
        state = await broadcast_collection.find_one({"_id": broadcast_id})
        return state

    async def list_active_broadcasts(self):
        broadcast_collection = self.db.broadcasts
        cursor = broadcast_collection.find({"is_cancelled": False})
        return await cursor.to_list(length=100)

    async def add_restart_message(self, message_id: int, chat_id: int) -> None:
        """Store the restart message ID and chat ID."""
        try:
            await self.restart_message_col.insert_one(
                {"message_id": message_id, "chat_id": chat_id, "timestamp": datetime.datetime.utcnow()}
            )
            logger.debug(f"Restart message {message_id} in chat {chat_id} saved to DB.")
        except Exception as e:
            logger.error(f"Database error saving restart message: {e}")

    async def get_restart_message(self) -> Optional[Dict[str, Any]]:
        """Retrieve the restart message from the database."""
        try:
            # Get the most recent restart message
            restart_msg = await self.restart_message_col.find_one(sort=[("timestamp", -1)])
            if restart_msg:
                logger.debug(f"Retrieved restart message {restart_msg['message_id']} from DB.")
            return restart_msg
        except Exception as e:
            logger.error(f"Database error retrieving restart message: {e}")
            return None

    async def delete_restart_message(self, message_id: int) -> None:
        """Delete the restart message from the database."""
        try:
            result = await self.restart_message_col.delete_one({"message_id": message_id})
            if result.deleted_count > 0:
                logger.debug(f"Restart message {message_id} deleted from DB.")
            else:
                logger.debug(f"Attempted to delete non-existent restart message {message_id}.")
        except Exception as e:
            logger.error(f"Database error deleting restart message: {e}")

    async def close(self):
        # Close database connection
        if self._client:
            self._client.close()

# Initialize the Database instance
db = Database(Var.DATABASE_URL, Var.NAME)
