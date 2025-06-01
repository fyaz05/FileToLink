# Thunder/utils/retry_api.py

import asyncio
import random
from typing import Callable, TypeVar, Any, Awaitable
from pyrogram.errors import FloodWait, RPCError
from Thunder.utils.logger import logger

T = TypeVar('T')
DEFAULT_RETRY_COUNT = 3
BASE_RETRY_DELAY = 1.0
MAX_FLOOD_WAIT = 30
MAX_JITTER_FACTOR = 0.1

async def retry_api_call(
    func: Callable[[], Awaitable[T]],
    max_retries: int = DEFAULT_RETRY_COUNT,
    base_delay: float = BASE_RETRY_DELAY,
    operation_name: str = "API call"
) -> T:
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                logger.debug(f"Retry attempt {attempt} for {operation_name}.")
            return await func()
        except FloodWait as e:
            if attempt == max_retries:
                logger.error(f"Max retries reached for {operation_name} due to FloodWait: {e}")
                raise
            wait_time = min(e.value, MAX_FLOOD_WAIT)
            logger.warning(f"FloodWait ({e.value}s) for {operation_name}, sleeping {wait_time}s before retry.")
            await asyncio.sleep(wait_time)
        except (asyncio.TimeoutError, RPCError) as e:
            if attempt == max_retries:
                logger.error(f"Max retries reached for {operation_name} due to {e.__class__.__name__}: {e}")
                raise
            delay = base_delay * (2 ** (attempt - 1))
            jitter = random.uniform(0, delay * MAX_JITTER_FACTOR)
            logger.warning(f"{e.__class__.__name__} during {operation_name}: {e}. Retrying in {delay + jitter:.2f}s.")
            await asyncio.sleep(delay + jitter)
        except Exception as e:
            if attempt == max_retries:
                logger.error(f"Max retries reached for {operation_name} due to unexpected error: {e}")
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(f"Unexpected error {e.__class__.__name__} during {operation_name}: {e}. Retrying in {delay}s.")
            await asyncio.sleep(delay)

async def retry_api_operation(
    client: Any,
    operation: str,
    *args: Any,
    max_retries: int = DEFAULT_RETRY_COUNT,
    base_delay: float = BASE_RETRY_DELAY,
    **kwargs: Any
) -> Any:
    kwargs.pop("operation_name", None)
    return await retry_api_call(
        lambda: getattr(client, operation)(*args, **kwargs),
        max_retries=max_retries,
        base_delay=base_delay
    )

async def retry_get_chat_member(client: Any, chat_id: int, user_id: int, **kwargs) -> Any:
    kwargs.pop("operation_name", None)
    return await retry_api_operation(
        client,
        "get_chat_member",
        chat_id,
        user_id,
        **kwargs
    )

async def retry_get_chat(client: Any, chat_id: int, **kwargs) -> Any:
    kwargs.pop("operation_name", None)
    return await retry_api_operation(
        client,
        "get_chat",
        chat_id,
        **kwargs
    )

async def retry_send_message(client: Any, chat_id: int, text: str, **kwargs) -> Any:
    kwargs.pop("operation_name", None)
    current_max_retries = kwargs.pop("max_retries", DEFAULT_RETRY_COUNT)
    current_base_delay = kwargs.pop("base_delay", BASE_RETRY_DELAY)
    return await retry_api_call(
        lambda: client.send_message(chat_id=chat_id, text=text, **kwargs),
        max_retries=current_max_retries,
        base_delay=current_base_delay
    )
