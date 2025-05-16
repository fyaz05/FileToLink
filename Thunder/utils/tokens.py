"""
Token generation, verification, and authorization management for the Thunder bot.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, AsyncIterator

from motor.motor_asyncio import AsyncIOMotorCursor
from Thunder.utils.database import Database
from Thunder.utils.logger import logger
from Thunder.vars import Var

# Initialize database
db = Database(Var.DATABASE_URL, Var.NAME)

async def check(user_id: int) -> bool:
    """
    Verify if a user has a valid token.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        bool: True if user has valid token, False otherwise
    """
    # Implementation will check if user has valid token in database
    if not getattr(Var, "TOKEN_ENABLED", False):
        return True  # Token system disabled
    
    # Check if user is owner or authorized
    if user_id in Var.OWNER_ID or await allowed(user_id):
        return True
    
    # Check for valid token
    token_data = await db.token_col.find_one({"user_id": user_id})
    if not token_data:
        return False
    
    # Check if token is expired
    if token_data["expires_at"] <= datetime.utcnow():
        return False
    
    return True

async def generate(user_id: int) -> Dict[str, Any]:
    """
    Generate a new token for a user.
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        dict: Token data with expiration time
    """
    # Implementation will generate unique token and store in database
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

async def allowed(user_id: int) -> bool:
    """
    Check if a user is authorized (permanently allowed).
    
    Args:
        user_id: User's Telegram ID
        
    Returns:
        bool: True if user is authorized, False otherwise
    """
    # Implementation will check if user is in authorized list
    auth_data = await db.authorized_users_col.find_one({"user_id": user_id})
    return bool(auth_data)

async def authorize(user_id: int, authorized_by: int) -> bool:
    """
    Authorize a user to use the bot permanently.
    
    Args:
        user_id: User's Telegram ID to authorize
        authorized_by: Admin's Telegram ID who authorized
        
    Returns:
        bool: True if successful, False otherwise
    """
    # Implementation will add user to authorized list
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
    # Implementation will remove user from authorized list
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
    # Implementation will retrieve token data
    return await db.token_col.find_one({"user_id": user_id})

async def get(token: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve token record by token string.
    """
    return await db.token_col.find_one({"token": token})

async def list_allowed() -> AsyncIOMotorCursor:
    """
    Get all authorized users.
    
    Returns:
        AsyncIOMotorCursor: Database cursor with authorized users
    """
    # Implementation will retrieve all authorized users
    return db.authorized_users_col.find({})

async def list_tokens() -> AsyncIOMotorCursor:
    """
    Get all active tokens.
    
    Returns:
        AsyncIOMotorCursor: Database cursor with active tokens
    """
    # Implementation will retrieve all tokens
    return db.token_col.find({})

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
            clean_token = token[5:]
            
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
            "description": "Premium access token"
        }
        
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        return {
            "valid": False,
            "reason": f"An error occurred during validation: {str(e)}",
            "token": token
        }
