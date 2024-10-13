import asyncio
import logging
import traceback
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked, PeerIdInvalid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def send_msg(user_id, message):
    """Attempt to forward a message to a specified user and handle exceptions."""
    try:
        await message.forward(chat_id=user_id)
        logging.info(f"Message successfully sent to {user_id}")
        return 200, None  # Success code

    except FloodWait as e:
        logging.warning(f"FloodWait error: sleeping for {e.x} seconds")
        await asyncio.sleep(e.x)
        return await send_msg(user_id, message)  # Retry after wait

    except InputUserDeactivated:
        error_msg = f"{user_id} : deactivated"
        logging.error(error_msg)
        return 400, error_msg

    except UserIsBlocked:
        error_msg = f"{user_id} : blocked the bot"
        logging.error(error_msg)
        return 400, error_msg

    except PeerIdInvalid:
        error_msg = f"{user_id} : user id invalid"
        logging.error(error_msg)
        return 400, error_msg

    except Exception:
        error_msg = f"{user_id} : {traceback.format_exc()}"
        logging.error(f"Unexpected error: {error_msg}")
        return 500, error_msg
