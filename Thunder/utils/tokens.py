import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import asyncio # Moved from inside generate
import random # Moved from inside generate
import pyrogram.errors
from Thunder.utils.database import db
from Thunder.vars import Var
from Thunder.utils.error_handling import log_errors
from Thunder.utils.logger import logger

_OWNER_IDS_CACHE = None

def _get_owner_ids():
    global _OWNER_IDS_CACHE
    if _OWNER_IDS_CACHE is None:
        owner_id = Var.OWNER_ID
        _OWNER_IDS_CACHE = set(owner_id if isinstance(owner_id, (list, tuple, set)) else [owner_id])
    return _OWNER_IDS_CACHE

@log_errors
async def check(user_id: int) -> bool:
    logger.debug(f"Token validation started for user: {user_id}")
    if not getattr(Var, "TOKEN_ENABLED", False):
        logger.debug("Token system disabled - access granted")
        return True
    if user_id in _get_owner_ids():
        logger.debug("Owner access granted")
        return True
        
    current_time = datetime.utcnow()
    auth_result = await db.authorized_users_col.find_one(
        {"user_id": user_id},
        {"_id": 1}
    )
    if auth_result:
        return True
        
    token_result = await db.token_col.find_one(
        {"user_id": user_id, "expires_at": {"$gt": current_time}, "activated": True},
        {"_id": 1}
    )
    
    access_granted = bool(token_result)
    logger.debug(f"Token validation {'SUCCESS' if access_granted else 'FAILURE'} for user: {user_id}")
    return access_granted

@log_errors
async def generate(user_id: int) -> str:
    logger.debug(f"Token generation started for user: {user_id}")
 
    # Check for an existing unactivated token for this user
    existing_token_doc = await db.token_col.find_one(
        {"user_id": user_id, "activated": False, "expires_at": {"$gt": datetime.utcnow()}},
        {"token": 1}
    )
    if existing_token_doc:
        logger.debug(f"Returning existing unactivated token for user: {user_id}")
        return existing_token_doc["token"]

    token_str = secrets.token_urlsafe(32)
    masked_token = f"{token_str[:4]}...{token_str[-4:]}"
    logger.debug(f"Generated new token: {masked_token}")
    
    max_retries = 3
    base_delay = 0.5  # seconds
    for attempt in range(max_retries):
        try:
            ttl_hours = getattr(Var, "TOKEN_TTL_HOURS", 24)
            created_at = datetime.utcnow()
            expires_at = created_at + timedelta(hours=ttl_hours)

            await db.save_main_token(
                user_id=user_id,
                token_value=token_str,
                expires_at=expires_at,
                created_at=created_at,
                activated=False
            )
            logger.debug(f"New token generated and saved successfully for user: {user_id}")
            return token_str
        except pyrogram.errors.RPCError as e:
            logger.error(f"Telegram API error while generating new token for user {user_id}: {e}")
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                # Exponential backoff with jitter
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
                logger.warning(f"Database error (attempt {attempt+1}/{max_retries}) while saving new token: {e}. Retrying in {delay:.2f} seconds.")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Failed to generate and save new token for user {user_id} after {max_retries} attempts: {e}")
                raise
    return ""

@log_errors
async def allowed(user_id: int) -> bool:
    result = await db.authorized_users_col.find_one(
        {"user_id": user_id},
        {"_id": 1}
    )
    return bool(result)

@log_errors
async def authorize(user_id: int, authorized_by: int) -> bool:
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

@log_errors
async def deauthorize(user_id: int) -> bool:
    result = await db.authorized_users_col.delete_one({"user_id": user_id})
    return result.deleted_count > 0

@log_errors
async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    return await db.token_col.find_one({"user_id": user_id})

@log_errors
async def list_allowed() -> List[Dict[str, Any]]:
    cursor = db.authorized_users_col.find(
        {},
        {"user_id": 1, "authorized_by": 1, "authorized_at": 1}
    )
    return await cursor.to_list(length=None)

@log_errors
async def list_tokens() -> List[Dict[str, Any]]:
    current_time = datetime.utcnow()
    cursor = db.token_col.find(
        {"expires_at": {"$gt": current_time}},
        {"user_id": 1, "expires_at": 1, "created_at": 1, "activated": 1}
    )
    return await cursor.to_list(length=None)

@log_errors
async def cleanup_expired_tokens() -> int:
    current_time = datetime.utcnow()
    logger.debug("Cleaning up expired tokens")
    result = await db.token_col.delete_many({"expires_at": {"$lte": current_time}})
    logger.debug(f"Cleaned up {result.deleted_count} expired tokens")
    return result.deleted_count
