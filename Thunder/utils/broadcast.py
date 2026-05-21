import asyncio
import os
import time

import pytdbot
from pytdbot import types

from Thunder.utils.database import db
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_BROADCAST_COMPLETE,
    MSG_BROADCAST_START,
    MSG_BUTTON_CANCEL_BROADCAST,
    MSG_INVALID_BROADCAST_CMD,
)
from Thunder.utils.time_format import get_readable_time

broadcast_ids = {}


async def broadcast_message(client: pytdbot.Client, message: types.Message, mode: str = "all"):
    reply_to = getattr(message, "reply_to", None)
    replied_msg = None
    if reply_to and hasattr(reply_to, "message_id"):
        replied_msg = await client.getMessage(
            chat_id=message.chat_id, message_id=reply_to.message_id
        )
        if isinstance(replied_msg, types.Error):
            replied_msg = None

    if not replied_msg:
        try:
            await message.reply_text(MSG_INVALID_BROADCAST_CMD)
        except Exception as e:
            logger.error(f"Error sending invalid broadcast message: {e}", exc_info=True)
        return

    broadcast_id = os.urandom(3).hex()
    stats = {"total": 0, "success": 0, "failed": 0, "deleted": 0, "cancelled": False}
    broadcast_ids[broadcast_id] = stats

    cancel_button = types.InlineKeyboardButton(
        text=MSG_BUTTON_CANCEL_BROADCAST,
        type=types.InlineKeyboardButtonTypeCallback(data=f"cancel_{broadcast_id}".encode())
    )
    try:
        status_msg = await message.reply_text(
            MSG_BROADCAST_START,
            reply_markup=types.ReplyMarkupInlineKeyboard(rows=[[cancel_button]])
        )
    except Exception as e:
        logger.error(f"Error starting broadcast: {e}", exc_info=True)
        del broadcast_ids[broadcast_id]
        return

    if isinstance(status_msg, types.Error):
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
        del broadcast_ids[broadcast_id]
        return

    if stats["total"] == 0:
        del broadcast_ids[broadcast_id]
        return

    async def do_broadcast():
        async for user in cursor:
            if stats["cancelled"]:
                break

            user_id = user.get('id') or user.get('user_id')
            if not user_id:
                continue

            try:
                result = await client.sendCopy(
                    chat_id=user_id,
                    from_chat_id=message.chat_id,
                    message_id=replied_msg.id
                )
                if isinstance(result, types.Error):
                    if result.code in [400, 403]:
                        is_authorized = await db.is_user_authorized(user_id)
                        if not is_authorized:
                            await db.delete_user(user_id)
                            stats["deleted"] += 1
                        else:
                            stats["failed"] += 1
                    else:
                        stats["failed"] += 1
                else:
                    stats["success"] += 1
            except Exception as e:
                logger.error(f"Error copying message to user {user_id}: {e}", exc_info=True)
                stats["failed"] += 1

        try:
            await status_msg.delete()
        except Exception as e:
            logger.debug(f"Could not delete status message: {e}")

        completion_msg = MSG_BROADCAST_COMPLETE.format(
            elapsed_time=get_readable_time(int(time.time() - start_time)),
            total_users=stats["total"],
            successes=stats["success"],
            failures=stats["failed"],
            deleted_accounts=stats["deleted"]
        )

        if stats["cancelled"]:
            completion_msg = "🛑 **Broadcast Cancelled**\n\n" + completion_msg

        try:
            await message.reply_text(completion_msg)
        except Exception as e:
            logger.error(f"Failed to send broadcast completion message: {e}", exc_info=True)

        if broadcast_id in broadcast_ids:
            del broadcast_ids[broadcast_id]

    asyncio.create_task(do_broadcast())
