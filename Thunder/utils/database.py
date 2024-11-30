# Thunder/utils/database.py

import datetime
from typing import Optional
import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorCollection


class Database:
    """
    Database class for handling user data using MongoDB.
    """

    def __init__(self, uri: str, database_name: str):
        """
        Initialize the database connection.

        Args:
            uri (str): The MongoDB URI.
            database_name (str): The name of the database.
        """
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col: AsyncIOMotorCollection = self.db.users

    def new_user(self, user_id: int) -> dict:
        """
        Create a new user document.

        Args:
            user_id (int): The user ID.

        Returns:
            dict: The user document.
        """
        return {
            'id': user_id,
            'join_date': datetime.datetime.utcnow()
        }

    async def add_user(self, user_id: int):
        """
        Add a new user to the database.

        Args:
            user_id (int): The user ID.
        """
        if not await self.is_user_exist(user_id):
            user = self.new_user(user_id)
            await self.col.insert_one(user)

    async def add_user_pass(self, user_id: int, ag_pass: str):
        """
        Add or update the user's password.

        Args:
            user_id (int): The user ID.
            ag_pass (str): The password to set.
        """
        await self.add_user(user_id)
        await self.col.update_one({'id': user_id}, {'$set': {'ag_p': ag_pass}})

    async def get_user_pass(self, user_id: int) -> Optional[str]:
        """
        Retrieve the user's password.

        Args:
            user_id (int): The user ID.

        Returns:
            Optional[str]: The password if set, else None.
        """
        user_pass = await self.col.find_one({'id': user_id}, {'ag_p': 1})
        return user_pass.get('ag_p') if user_pass else None

    async def is_user_exist(self, user_id: int) -> bool:
        """
        Check if a user exists in the database.

        Args:
            user_id (int): The user ID.

        Returns:
            bool: True if the user exists, False otherwise.
        """
        user = await self.col.find_one({'id': user_id}, {'_id': 1})
        return bool(user)

    async def total_users_count(self) -> int:
        """
        Get the total number of users.

        Returns:
            int: The total user count.
        """
        count = await self.col.count_documents({})
        return count

    async def get_all_users(self):
        """
        Retrieve all users.

        Returns:
            AsyncIOMotorCursor: A cursor to iterate over users.
        """
        all_users = self.col.find({})
        return all_users

    async def delete_user(self, user_id: int):
        """
        Delete a user from the database.

        Args:
            user_id (int): The user ID.
        """
        await self.col.delete_one({'id': user_id})
