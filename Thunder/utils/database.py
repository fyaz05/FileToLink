# Thunder/utils/database.py

import datetime
from typing import Optional, List, Dict, Any
import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorCollection

class Database:
    # Database class for handling user data using MongoDB
    
    def __init__(self, uri: str, database_name: str):
        # Initialize database connection
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col: AsyncIOMotorCollection = self.db.users
    
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
        
    async def close(self):
        # Close database connection
        if self._client:
            self._client.close()
