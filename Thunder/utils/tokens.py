"""
Token generation, verification, and authorization management for the Thunder bot.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, AsyncIterator, List, Union
from motor.motor_asyncio import AsyncIOMotorCursor

from Thunder.utils.database import db
from Thunder.utils.logger import logger
from Thunder.vars import Var

async def check(user_id: int) -> bool:
    """
    Verify if a user has a valid token.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        bool: True if user has valid token, False otherwise
    """
    try:
        # Check if token system is enabled
        if not getattr(Var, "TOKEN_ENABLED", False):
            return True  # Token system disabled
            
        # Check if user is owner
        if isinstance(Var.OWNER_ID, (list, tuple, set)):
            is_owner = user_id in Var.OWNER_ID
        else:
            is_owner = user_id == Var.OWNER_ID
            
        # Check if user is owner or authorized
        if is_owner or await allowed(user_id):
            return True
            
        # Check for valid token
        token_data = await db.token_col.find_one({"user_id": user_id})
        if not token_data:
            return False
            
        # Check if token is expired
        if token_data["expires_at"] <= datetime.utcnow():
            return False
            
        return True
    except Exception as e:
        logger.error(f"Error in token check for user {user_id}: {e}")
        return False

async def generate(user_id: int) -> Dict[str, Any]:
    """
    Generate a new token for a user.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        dict: Token data with expiration time
    """
    try:
        duration = getattr(Var, "TOKEN_TTL_HOURS", 24)
        token = str(uuid.uuid4())
        created_at = datetime.utcnow()
        expires_at = created_at + timedelta(hours=duration)
        
        token_data = {
            "user_id": user_id,
            "token": token,
            "created_at": created_at,
            "expires_at": expires_at
        }
        
        # Store or update token in database
        await db.token_col.update_one(
            {"user_id": user_id},
            {"$set": token_data},
            upsert=True
        )
        
        return token_data
    except Exception as e:
        logger.error(f"Failed to generate token for user {user_id}: {e}")
        raise

async def allowed(user_id: int) -> bool:
    """
    Check if a user is authorized (permanently allowed).
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        bool: True if user is authorized, False otherwise
    """
    try:
        auth_data = await db.authorized_users_col.find_one({"user_id": user_id})
        return bool(auth_data)
    except Exception as e:
        logger.error(f"Error checking authorization for user {user_id}: {e}")
        return False

async def authorize(user_id: int, authorized_by: int) -> bool:
    """
    Authorize a user to use the bot permanently.
    
    Args:
        user_id: User's Telegram ID to authorize
        authorized_by: Admin's Telegram ID who authorized
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        auth_data = {
            "user_id": user_id,
            "authorized_by": authorized_by,
            "authorized_at": datetime.utcnow()
        }
        
        await db.authorized_users_col.update_one(
            {"user_id": user_id},
            {"$set": auth_data},
            upsert=True
        )
        
        return True
    except Exception as e:
        logger.error(f"Failed to authorize user {user_id}: {e}")
        return False

async def deauthorize(user_id: int) -> bool:
    """
    Remove a user's authorization.
    
    Args:
        user_id: User's Telegram ID to deauthorize
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        result = await db.authorized_users_col.delete_one({"user_id": user_id})
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Failed to deauthorize user {user_id}: {e}")
        return False

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Get information about a user's token.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        dict or None: Token data if found, None otherwise
    """
    try:
        return await db.token_col.find_one({"user_id": user_id})
    except Exception as e:
        logger.error(f"Failed to get token data for user {user_id}: {e}")
        return None

async def get(token: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve token record by token string.
    
    Args:
        token: Token string to look up
        
    Returns:
        dict or None: Token data if found, None otherwise
    """
    try:
        return await db.token_col.find_one({"token": token})
    except Exception as e:
        logger.error(f"Failed to get token data: {e}")
        return None

async def list_allowed() -> List[Dict[str, Any]]:
    """
    Get all authorized users.
    
    Returns:
        List[Dict[str, Any]]: List of authorized users data
    """
    try:
        cursor = db.authorized_users_col.find({})
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.error(f"Failed to list authorized users: {e}")
        return []

async def list_tokens() -> List[Dict[str, Any]]:
    """
    Get all active tokens.
    
    Returns:
        List[Dict[str, Any]]: List of active tokens data
    """
    try:
        cursor = db.token_col.find({})
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.error(f"Failed to list tokens: {e}")
        return []

async def validate_activation_token(token: str) -> Dict[str, Any]:
    """
    Validates an activation token and returns validation status.
    
    Args:
        token: The token string to validate
        
    Returns:
        dict: Validation result with status and details
    """
    try:
        # Remove 'token' prefix if present
        clean_token = token
        if token.startswith("token"):
            # Handle different possible separators
            if len(token) > 5 and token[5] in ('-', '_'):
                clean_token = token[6:]  # Skip prefix and separator
            else:
                clean_token = token[5:]  # Skip just the prefix
                
        # Check if token exists in database
        token_record = await get(clean_token)
        if not token_record:
            return {
                "valid": False,
                "reason": "Token does not exist or has been revoked",
                "token": clean_token
            }
            
        # Check if token is expired
        if token_record["expires_at"] <= datetime.utcnow():
            return {
                "valid": False,
                "reason": "Token has expired",
                "token": clean_token,
                "expiry_date": token_record["expires_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
            }
            
        # Token is valid
        return {
            "valid": True,
            "token": clean_token,
            "user_id": token_record["user_id"],
            "expiry_date": token_record["expires_at"].strftime("%Y-%m-%d %H:%M:%S UTC"),
            "created_at": token_record["created_at"].strftime("%Y-%m-%d %H:%M:%S UTC"),
            "description": "Access token"
        }
        
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        return {
            "valid": False,
            "reason": f"An error occurred during validation: {str(e)}",
            "token": token
        }
