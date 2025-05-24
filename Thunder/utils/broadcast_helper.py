# Thunder/utils/broadcast_helper.py

import asyncio
import traceback
from typing import Tuple

from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid
from pyrogram.types import Message
from Thunder.utils.logger import logger


async def send_msg(user_id: int, message: Message) -> Tuple[int, str]:
    # Attempt to forward a message to a user and handle exceptions
    try:
        await message.forward(chat_id=user_id)
        return 200, None  # Success code

    except FloodWait as e:
        logger.warning(f"FloodWait error: sleeping for {e.value} seconds.")
        await asyncio.sleep(e.value + 1)
        return await send_msg(user_id, message)  # Retry after wait

    except InputUserDeactivated:
        error_msg = f"{user_id} : deactivated"
        logger.error(error_msg)
        return 400, error_msg

    except UserIsBlocked:
        error_msg = f"{user_id} : blocked the bot"
        logger.error(error_msg)
        return 400, error_msg

    except PeerIdInvalid:
        error_msg = f"{user_id} : user ID invalid"
        logger.error(error_msg)
        return 400, error_msg

    except Exception as e:
        error_msg = f"{user_id} : {traceback.format_exc()}"
        logger.error(f"Unexpected error: {error_msg}", exc_info=True)
        return 500, error_msg
