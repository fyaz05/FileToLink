import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
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
    if not getattr(Var, "TOKEN_ENABLED", False):
        return True
    if user_id in _get_owner_ids():
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
    return bool(token_result)

@log_errors
async def generate(user_id: int) -> str:
    token_str = secrets.token_urlsafe(32)
    ttl_hours = getattr(Var, "TOKEN_TTL_HOURS", 24)
    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(hours=ttl_hours)

    try:
        await db.save_main_token(
            user_id=user_id,
            token_value=token_str,
            created_at=created_at,
            expires_at=expires_at,
            activated=False
        )
        return token_str
    except Exception as e:
        logger.error(f"Failed to generate and save unactivated token for user {user_id}: {e}")
        raise

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
async def get(token: str) -> Optional[Dict[str, Any]]:
    return await db.token_col.find_one({"token": token})

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
    result = await db.token_col.delete_many({"expires_at": {"$lte": datetime.utcnow()}})
    return result.deleted_count
