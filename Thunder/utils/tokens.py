import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from Thunder.utils.database import db
from Thunder.vars import Var
from Thunder.utils.error_handling import log_errors

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
        {"user_id": user_id, "expires_at": {"$gt": current_time}},
        {"_id": 1}
    )
    return bool(token_result)

@log_errors
async def generate(user_id: int) -> Dict[str, Any]:
    duration = getattr(Var, "TOKEN_TTL_HOURS", 24)
    token = secrets.token_urlsafe(32)
    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(hours=duration)
    token_data = {
        "user_id": user_id,
        "token": token,
        "created_at": created_at,
        "expires_at": expires_at
    }
    await db.token_col.update_one(
        {"user_id": user_id},
        {"$set": token_data},
        upsert=True
    )
    return token_data

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
        {"user_id": 1, "expires_at": 1, "created_at": 1}
    )
    return await cursor.to_list(length=None)

@log_errors
async def validate_activation_token(token: str) -> Dict[str, Any]:
    clean_token = token
    if token.startswith("token"):
        clean_token = token[6:] if len(token) > 5 and token[5] in ('-', '_') else token[5:]
    current_time = datetime.utcnow()
    token_record = await db.token_col.find_one({"token": clean_token})
    if not token_record:
        return {
            "valid": False,
            "reason": "Token does not exist or has been revoked",
            "token": clean_token
        }
    if token_record["expires_at"] <= current_time:
        return {
            "valid": False,
            "reason": "Token has expired",
            "token": clean_token,
            "expiry_date": token_record["expires_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
        }
    return {
        "valid": True,
        "token": clean_token,
        "user_id": token_record["user_id"],
        "expiry_date": token_record["expires_at"].strftime("%Y-%m-%d %H:%M:%S UTC"),
        "created_at": token_record["created_at"].strftime("%Y-%m-%d %H:%M:%S UTC"),
        "description": "Access token"
    }

@log_errors
async def cleanup_expired_tokens() -> int:
    result = await db.token_col.delete_many({"expires_at": {"$lte": datetime.utcnow()}})
    return result.deleted_count
