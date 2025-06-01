# Thunder/utils/broadcast_helper.py

import asyncio
from typing import Tuple
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import Message
from Thunder.utils.error_handling import log_errors

@log_errors
async def send_msg(user_id: int, message: Message) -> Tuple[int, str]:
    try:
        await message.forward(chat_id=user_id)
        return 200, None
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        return await send_msg(user_id, message)
    except InputUserDeactivated:
        return 400, f"{user_id} : deactivated"
    except UserIsBlocked:
        return 400, f"{user_id} : blocked the bot"
    except PeerIdInvalid:
        return 400, f"{user_id} : user ID invalid"
    except Exception as e:
        return 500, f"{user_id} : {str(e)}"
