# Thunder/utils/broadcast.py

import asyncio
import os
import time
from typing import Any

from pyrogram.client import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import (
    ChannelInvalid,
    ChatWriteForbidden,
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    UserDeactivated,
    UserIsBlocked,
)
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from Thunder.utils.database import db
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BROADCAST_CANCELLED_PREFIX,
    MSG_BROADCAST_COMPLETE,
    MSG_BROADCAST_FAILED_USERS,
    MSG_BROADCAST_NO_USERS,
    MSG_BROADCAST_PROGRESS,
    MSG_BROADCAST_START,
    MSG_BUTTON_CANCEL_BROADCAST,
    MSG_INVALID_BROADCAST_CMD,
)
from Thunder.utils.safe_call import reply_safe, tg_call
from Thunder.utils.time_format import get_readable_time
from Thunder.vars import Var

broadcast_ids: dict[str, dict[str, Any]] = {}

# Errors that mean the recipient will never be reachable again.
_PERMANENT_ERRORS = (
    UserDeactivated,
    UserIsBlocked,
    PeerIdInvalid,
    ChatWriteForbidden,
    ChannelInvalid,
    InputUserDeactivated,
)

# Closed mapping for the exact exception set above -- no dead fallback branch.
_PERMANENT_ERROR_REASONS: dict[type[Exception], tuple[str, str]] = {
    ChannelInvalid: ("Channel", "invalid channel"),
    InputUserDeactivated: ("User", "deactivated account"),
    UserIsBlocked: ("User", "blocked the bot"),
    UserDeactivated: ("User", "deactivated account"),
    PeerIdInvalid: ("Recipient", "invalid ID"),
    ChatWriteForbidden: ("Chat", "write forbidden"),
}

# pacing between sends per worker + progress-edit cadence (M4a)
_BROADCAST_PACE_SECONDS = 0.2
_PROGRESS_EVERY = 25

# strong refs: CPython only weakly references tasks; unreferenced ones
# can be garbage-collected mid-run
_BROADCAST_TASKS: set[asyncio.Task] = set()


async def broadcast_message(client: Client, message: Message, mode: str = "all"):
    if not message.reply_to_message:
        try:
            await reply_safe(message, MSG_INVALID_BROADCAST_CMD)
        except Exception as e:
            logger.error(f"Error sending invalid broadcast message: {e}", exc_info=True)
        return

    broadcast_id = os.urandom(3).hex()
    stats = {"total": 0, "success": 0, "failed": 0, "deleted": 0, "cancelled": False}
    broadcast_ids[broadcast_id] = stats

    try:
        status_msg = await tg_call(
            message.reply_text,
            MSG_BROADCAST_START,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            MSG_BUTTON_CANCEL_BROADCAST, callback_data=f"cancel_{broadcast_id}"
                        )
                    ]
                ]
            ),
        )
    except Exception as e:
        logger.error(f"Error starting broadcast: {e}", exc_info=True)
        del broadcast_ids[broadcast_id]
        return

    start_time = time.time()

    try:
        if mode == "authorized":
            stats["total"] = await db.get_authorized_users_count()
            cursor = await db.get_authorized_users_cursor()
        elif mode == "regular":
            stats["total"] = await db.get_regular_users_count()
            cursor = await db.get_regular_users_cursor()
        else:
            stats["total"] = await db.total_users_count()
            cursor = await db.get_all_users()
    except Exception as e:
        logger.error(f"Error getting user cursor for mode '{mode}': {e}", exc_info=True)
        try:
            await tg_call(
                status_msg.edit_text, MSG_BROADCAST_FAILED_USERS.format(mode=mode), retries=0
            )
        except Exception:
            pass
        del broadcast_ids[broadcast_id]
        return

    if stats["total"] == 0:
        try:
            await tg_call(status_msg.edit_text, MSG_BROADCAST_NO_USERS.format(mode=mode), retries=0)
        except Exception:
            pass
        del broadcast_ids[broadcast_id]
        return

    async def do_broadcast():
        # M4a: bounded-queue worker pool; cursor streamed, never materialized;
        # sends paced, progress edits throttled, cancel stays responsive
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        async def producer():
            try:
                async for user in cursor:
                    if stats["cancelled"]:
                        break
                    await queue.put(user)
            except Exception as e:
                logger.error(f"Broadcast cursor error: {e}", exc_info=True)
            finally:
                for _ in range(worker_count):
                    await queue.put(None)  # poison pills

        async def worker():
            while True:
                user = await queue.get()
                try:
                    if user is None:
                        return
                    if stats["cancelled"]:
                        continue
                    user_id = user.get("id") or user.get("user_id")
                    if not user_id:
                        logger.warning(f"Skipping user with no ID: {user}")
                        continue
                    await _send_one(client, message, user_id, stats)
                    if stats["success"] and stats["success"] % _PROGRESS_EVERY == 0:
                        await _edit_progress(status_msg, stats)
                finally:
                    if user is not None:
                        await asyncio.sleep(_BROADCAST_PACE_SECONDS)

        worker_count = Var.BROADCAST_WORKERS
        workers = [
            asyncio.create_task(worker(), name=f"broadcast_worker_{i}") for i in range(worker_count)
        ]
        producer_task = asyncio.create_task(producer(), name="broadcast_producer")

        completed_normally = False
        try:
            await producer_task
            results = await asyncio.gather(*workers, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
                    logger.error(f"Broadcast worker failed: {r!r}")
            completed_normally = True
        finally:
            # no worker/producer/status/registry leakage on ANY exit path
            for t in workers:
                if not t.done():
                    t.cancel()
            if not producer_task.done():
                producer_task.cancel()
            try:
                await tg_call(status_msg.delete, retries=0)
            except Exception:
                pass
            broadcast_ids.pop(broadcast_id, None)

        if completed_normally:
            completion_msg = MSG_BROADCAST_COMPLETE.format(
                elapsed_time=get_readable_time(int(time.time() - start_time)),
                total_users=stats["total"],
                successes=stats["success"],
                failures=stats["failed"],
                deleted_accounts=stats["deleted"],
            )

            if stats["cancelled"]:
                completion_msg = MSG_BROADCAST_CANCELLED_PREFIX + completion_msg

            try:
                await reply_safe(message, completion_msg, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Failed to send broadcast completion message: {e}", exc_info=True)

    task = asyncio.create_task(do_broadcast())
    # hold a strong ref so CPython cannot GC the task mid-run
    _BROADCAST_TASKS.add(task)
    task.add_done_callback(_BROADCAST_TASKS.discard)


async def _send_one(client: Client, message: Message, user_id: int, stats: dict) -> None:
    try:
        await tg_call(message.reply_to_message.copy, user_id, retries=2)
        stats["success"] += 1
    except _PERMANENT_ERRORS as e:
        # except matches subclasses; the dict keys are exact types, so an
        # unmapped subclass would raise KeyError inside the worker
        recipient_type, reason = _PERMANENT_ERROR_REASONS.get(type(e), ("Recipient", "unreachable"))

        logger.warning(f"{recipient_type} {user_id} removed due to {reason}")
        try:
            is_authorized = await db.is_user_authorized(user_id)
            if not is_authorized:
                await db.delete_user(user_id)
                stats["deleted"] += 1
            else:
                stats["failed"] += 1
        except Exception as db_err:
            logger.error(f"Prune lookup failed for {user_id}: {db_err}", exc_info=True)
            stats["failed"] += 1
    except FloodWait as e:
        logger.warning(f"FloodWait persisted for user {user_id}, last wait: {e.value}s")
        stats["failed"] += 1
    except Exception as e:
        logger.error(f"Error copying message to user {user_id}: {e}", exc_info=True)
        stats["failed"] += 1


async def _edit_progress(status_msg: Message, stats: dict) -> None:
    try:
        await tg_call(
            status_msg.edit_text,
            MSG_BROADCAST_PROGRESS.format(success=stats["success"], total=stats["total"]),
            retries=0,
        )
    except Exception:
        pass
