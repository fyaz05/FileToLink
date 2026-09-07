# Thunder/utils/tokens.py

import asyncio
import random
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from Thunder.utils.database import db
from Thunder.utils.flag_cache import flags
from Thunder.utils.logger import logger
from Thunder.vars import Var


def _invalidate_user_flags(user_id: int) -> None:
    flags.invalidate(("allowed", user_id), ("token_ok", user_id))


async def check(user_id: int) -> bool:
    """Token/authorization gate (H7: cached, fail-closed)."""
    try:
        if not Var.TOKEN_ENABLED:
            return True
        if user_id == Var.OWNER_ID:
            return True
        # cached authorized-user lookup (5 min TTL)
        if await allowed(user_id):
            return True
        return await flags.get_or_load(
            ("token_ok", user_id),
            lambda: _load_token_ok(user_id),
        )
    except Exception as e:
        logger.error(f"Error in check for user {user_id}: {e}", exc_info=True)
        raise


async def _load_token_ok(user_id: int) -> bool:
    """Loader for the activated-token flag.  Raises on DB failure so the
    caller can apply its fail-closed policy."""
    token_result = await db.token_col.find_one(
        {"user_id": user_id, "expires_at": {"$gt": datetime.now(UTC)}, "activated": True},
        {"_id": 1},
    )
    return bool(token_result)


async def generate(user_id: int) -> str:
    try:
        logger.debug(f"Token generation started for user: {user_id}")
        existing_token_doc = await db.token_col.find_one(
            {
                "user_id": user_id,
                "activated": False,
                "expires_at": {"$gt": datetime.now(UTC)},
            },
            {"token": 1},
        )
        if existing_token_doc:
            logger.debug(f"Returning existing unactivated token for user: {user_id}")
            return existing_token_doc["token"]
        token_str = secrets.token_urlsafe(32)
        max_retries = 3
        base_delay = 0.5
        for attempt in range(max_retries):
            try:
                ttl_hours = Var.TOKEN_TTL_HOURS
                created_at = datetime.now(UTC)
                expires_at = created_at + timedelta(hours=ttl_hours)
                await db.save_main_token(
                    user_id=user_id,
                    token_value=token_str,
                    expires_at=expires_at,
                    created_at=created_at,
                    activated=False,
                )
                logger.debug(f"New token generated and saved successfully for user: {user_id}")
                return token_str
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0, 0.1)
                    logger.warning(
                        f"Database error (attempt {attempt + 1}/{max_retries}) while saving new token: {e}. Retrying in {delay:.2f} seconds.",
                        exc_info=True,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Failed to generate and save new token for user {user_id} after {max_retries} attempts: {e}",
                        exc_info=True,
                    )
                    raise
        # The loop always returns or raises on its final attempt; crash loudly
        # instead of returning "", which callers would treat as a real token value.
        raise RuntimeError("token save retry loop exited without success")
    except Exception as e:
        logger.error(f"Error in generate for user {user_id}: {e}", exc_info=True)
        raise


async def consume(token: str, user_id: int) -> tuple[str, float]:
    """Atomically activate a token (plan M8).

    ``find_one_and_update`` conditioned on ``activated != True``: exactly one
    concurrent activation can win.  Returns ``(status, hours_valid)`` with
    status one of ``"ok" | "already" | "wrong_user" | "invalid"``.
    """
    now = datetime.now(UTC)
    try:
        doc = await db.token_col.find_one({"token": token})
        if not doc:
            return "invalid", 0.0
        if doc.get("user_id") != user_id:
            return "wrong_user", 0.0
        if doc.get("activated"):
            return "already", 0.0
        if doc.get("expires_at") and doc["expires_at"] <= now:
            # a stale deep-link must not activate a token that expired before the CAS
            return "invalid", 0.0

        expires_at = now + timedelta(hours=Var.TOKEN_TTL_HOURS)
        activated_doc = await db.token_col.find_one_and_update(
            {
                "token": token,
                "user_id": user_id,
                "activated": {"$ne": True},
                "expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "activated": True,
                    "activated_at": now,
                    "created_at": now,
                    "expires_at": expires_at,
                }
            },
            return_document=True,
        )
        if activated_doc is None:
            # Lost the CAS race: distinguish a concurrent activation (doc now
            # activated) from a doc that expired between pre-check and CAS;
            # both used to surface as the misleading "already".
            current = await db.token_col.find_one({"token": token}, {"activated": 1})
            if current and current.get("activated"):
                return "already", 0.0
            return "invalid", 0.0
        _invalidate_user_flags(user_id)
        hours = round((expires_at - now).total_seconds() / 3600, 1)
        logger.debug(f"Token atomically activated for user {user_id} ({hours}h)")
        return "ok", hours
    except Exception as e:
        logger.error(f"Error in consume for user {user_id}: {e}", exc_info=True)
        raise


async def allowed(user_id: int) -> bool:
    """Cached authorized-user check (H7).  Raises on DB failure."""
    return await flags.get_or_load(
        ("allowed", user_id),
        # delegate: two copies of the same existence check would drift
        lambda: db.is_user_authorized(user_id),
    )


async def authorize(user_id: int, authorized_by: int) -> bool:
    try:
        auth_data = {
            "user_id": user_id,
            "authorized_by": authorized_by,
            "authorized_at": datetime.now(UTC),
        }
        await db.authorized_users_col.update_one(
            {"user_id": user_id}, {"$set": auth_data}, upsert=True
        )
        _invalidate_user_flags(user_id)
        return True
    except Exception as e:
        logger.error(f"Error in authorize for user {user_id}: {e}", exc_info=True)
        raise


async def deauthorize(user_id: int) -> bool:
    try:
        result = await db.authorized_users_col.delete_one({"user_id": user_id})
        _invalidate_user_flags(user_id)
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Error in deauthorize for user {user_id}: {e}", exc_info=True)
        raise


async def list_allowed() -> list[dict[str, Any]]:
    try:
        cursor = db.authorized_users_col.find(
            {}, {"user_id": 1, "authorized_by": 1, "authorized_at": 1}
        )
        return await cursor.to_list(length=None)
    except Exception as e:
        logger.error(f"Error in list_allowed: {e}", exc_info=True)
        return []


async def cleanup_expired_tokens() -> int:
    try:
        current_time = datetime.now(UTC)
        logger.debug("Cleaning up expired tokens")
        result = await db.token_col.delete_many({"expires_at": {"$lte": current_time}})
        logger.debug(f"Cleaned up {result.deleted_count} expired tokens")
        return result.deleted_count
    except Exception as e:
        logger.error(f"Error in cleanup_expired_tokens: {e}", exc_info=True)
        return 0
