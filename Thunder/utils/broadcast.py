# Thunder/utils/broadcast.py

import asyncio
import os
import time

from pyrogram.client import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import (ChatWriteForbidden, FloodWait, PeerIdInvalid, UserDeactivated,
                             UserIsBlocked, ChannelInvalid, InputUserDeactivated)
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            Message)

from Thunder.utils.database import db
from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_INVALID_BROADCAST_CMD,
    MSG_BROADCAST_START,
    MSG_BUTTON_CANCEL_BROADCAST,
    MSG_BROADCAST_COMPLETE
)
from Thunder.utils.time_format import get_readable_time


broadcast_ids = {}

async def broadcast_message(client: Client, message: Message, mode: str = "all"):
    if not message.reply_to_message:
        try:
            await message.reply_text(MSG_INVALID_BROADCAST_CMD)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.reply_text(MSG_INVALID_BROADCAST_CMD)
        return

    broadcast_id = os.urandom(3).hex()
    stats = {"total": 0, "success": 0, "failed": 0, "deleted": 0, "cancelled": False}
    broadcast_ids[broadcast_id] = stats

    try:
        status_msg = await message.reply_text(
            MSG_BROADCAST_START,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(MSG_BUTTON_CANCEL_BROADCAST, callback_data=f"cancel_{broadcast_id}")
            ]])
        )
    except FloodWait as e:
        await asyncio.sleep(e.value)
        status_msg = await message.reply_text(
            MSG_BROADCAST_START,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(MSG_BUTTON_CANCEL_BROADCAST, callback_data=f"cancel_{broadcast_id}")
            ]])
        )

    start_time = time.time()

    if mode == "authorized":
        stats["total"] = await db.get_authorized_users_count()
        cursor = db.get_authorized_users_cursor()
    elif mode == "regular":
        stats["total"] = await db.get_regular_users_count()
        cursor = await db.get_regular_users_cursor()
    else:
        stats["total"] = await db.total_users_count()
        cursor = db.get_all_users()

    async def do_broadcast():
        async for user in cursor:
            if stats["cancelled"]:
                break

            user_id = user.get('id') or user.get('user_id')
            if not user_id:
                logger.warning(f"Skipping user with no ID: {user}")
                continue

            try:
                msg = None
                # Retry loop for FloodWait
                for _ in range(3):
                    try:
                        msg = await message.reply_to_message.copy(user_id)
                        break
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                    except Exception:
                        raise # Raise other exceptions to be caught below

                if msg:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1

            except (UserDeactivated, UserIsBlocked, PeerIdInvalid, ChatWriteForbidden, ChannelInvalid, InputUserDeactivated) as e:
                if isinstance(e, ChannelInvalid):
                    recipient_type = "Channel"
                    reason = "invalid channel"
                elif isinstance(e, InputUserDeactivated):
                    recipient_type = "User"
                    reason = "deactivated account"
                elif isinstance(e, UserIsBlocked):
                    recipient_type = "User"
                    reason = "blocked the bot"
                elif isinstance(e, UserDeactivated):
                    recipient_type = "User"
                    reason = "deactivated account"
                elif isinstance(e, PeerIdInvalid):
                    recipient_type = "Recipient"
                    reason = "invalid ID"
                elif isinstance(e, ChatWriteForbidden):
                    recipient_type = "Chat"
                    reason = "write forbidden"
                else:
                    recipient_type = "Recipient"
                    reason = f"error: {type(e).__name__}"

                logger.warning(f"{recipient_type} {user_id} removed due to {reason}")

                if mode != "authorized":
                    await db.delete_user(user_id)
                    stats["deleted"] += 1

            except Exception as e:
                logger.error(f"Error copying message to user {user_id}: {e}", exc_info=True)
                stats["failed"] += 1

        try:
            await status_msg.delete()
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await status_msg.delete()

        try:
            await message.reply_text(
                MSG_BROADCAST_COMPLETE.format(
                    elapsed_time=get_readable_time(int(time.time() - start_time)),
                    total_users=stats["total"],
                    successes=stats["success"],
                    failures=stats["failed"],
                    deleted_accounts=stats["deleted"]
                ),
                parse_mode=ParseMode.MARKDOWN
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await message.reply_text(
                MSG_BROADCAST_COMPLETE.format(
                    elapsed_time=get_readable_time(int(time.time() - start_time)),
                    total_users=stats["total"],
                    successes=stats["success"],
                    failures=stats["failed"],
                    deleted_accounts=stats["deleted"]
                ),
                parse_mode=ParseMode.MARKDOWN
            )

        del broadcast_ids[broadcast_id]

    asyncio.create_task(do_broadcast())
