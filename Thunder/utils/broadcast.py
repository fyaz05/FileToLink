# Thunder/utils/broadcast.py

import asyncio
import os
import time

from pyrogram.client import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import (ChatWriteForbidden, PeerIdInvalid, UserDeactivated,
                             UserIsBlocked)
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            Message)

from Thunder.utils.database import db
from Thunder.utils.handler import handle_flood_wait
from Thunder.utils.logger import logger
from Thunder.utils.messages import *
from Thunder.utils.time_format import get_readable_time


broadcast_ids = {}

async def broadcast_message(client: Client, message: Message):
    if not message.reply_to_message:
        await handle_flood_wait(message.reply_text, MSG_INVALID_BROADCAST_CMD)
        return
    
    broadcast_id = os.urandom(3).hex()
    stats = {"total": 0, "success": 0, "failed": 0, "deleted": 0, "cancelled": False}
    broadcast_ids[broadcast_id] = stats
    
    status_msg = await handle_flood_wait(
        message.reply_text,
        MSG_BROADCAST_START,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(MSG_BUTTON_CANCEL_BROADCAST, callback_data=f"cancel_{broadcast_id}")
        ]])
    )
    
    start_time = time.time()
    stats["total"] = await db.total_users_count()
    
    async def do_broadcast():
        async for user in await db.get_all_users():
            if stats["cancelled"]:
                break
            try:
                if await handle_flood_wait(message.reply_to_message.copy, user['id']):
                    stats["success"] += 1
                else:
                    stats["failed"] += 1
            except (UserDeactivated, UserIsBlocked, PeerIdInvalid, ChatWriteForbidden) as e:
                logger.warning(f"User {user['id']} removed due to: {type(e).__name__}", exc_info=True)
                await db.delete_user(user['id'])
                stats["deleted"] += 1
            except Exception as e:
                logger.error(f"Error copying message to user {user['id']}: {e}", exc_info=True)
                stats["failed"] += 1
        
        await handle_flood_wait(status_msg.delete)
        await handle_flood_wait(
            message.reply_text,
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
