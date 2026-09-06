# Thunder/utils/rate_limiter.py

import time
import math
import asyncio
from collections import deque
from typing import Callable, Dict, Optional, Tuple
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError
from Thunder.utils.logger import logger
from Thunder.utils.database import db
from Thunder.utils.messages import (
    MSG_RATE_LIMIT_QUEUE_PRIORITY,
    MSG_RATE_LIMIT_QUEUE_REGULAR,
    MSG_RATE_LIMIT_QUEUE_FULL
)
from Thunder.vars import Var


class QueueFullError(Exception):
    pass


class RateLimiter:
    def __init__(self):
        self.request_queue: deque = deque()
        self.priority_queue: deque = deque()
        self.user_queue_counts: Dict[int, int] = {}

        self.request_event: asyncio.Event = asyncio.Event()
        self.request_lock: asyncio.Lock = asyncio.Lock()

        self.user_requests: Dict[int, deque] = {}
        self.global_requests: deque = deque()

        self.processing_times: deque = deque(maxlen=100)
        self.file_processing_times: Dict[str, deque] = {}
        self.average_processing_time: float = 1.0

        self.auth_cache: Dict[int, Tuple[bool, float]] = {}
        self.auth_cache_ttl_seconds: int = 300

        self._initialization_error = False
        self._load_configuration()

    def _load_configuration(self):
        try:
            self.max_requests_per_period = Var.MAX_FILES_PER_PERIOD
            self.rate_limit_period_seconds = Var.RATE_LIMIT_PERIOD_MINUTES * 60
            self.max_queue_size = Var.MAX_QUEUE_SIZE
            self.enabled = Var.RATE_LIMIT_ENABLED
            self.global_rate_limit_enabled = Var.GLOBAL_RATE_LIMIT
            self.max_global_requests_per_minute = Var.MAX_GLOBAL_REQUESTS_PER_MINUTE

            if not self._validate_configuration():
                logger.warning("Rate limiter disabled due to invalid configuration.")
                self.enabled = False
            else:
                logger.debug(f"Rate limiter initialized: enabled={self.enabled}, "
                             f"max_requests={self.max_requests_per_period}, "
                             f"period={self.rate_limit_period_seconds}s, "
                             f"queue_size={self.max_queue_size}, "
                             f"global_enabled={self.global_rate_limit_enabled}, "
                             f"max_global_requests={self.max_global_requests_per_minute}")
        except Exception as e:
            logger.critical(f"Critical error initializing rate limiter, using safe defaults: {e}", exc_info=True)
            self.max_requests_per_period = 5
            self.rate_limit_period_seconds = 60
            self.max_queue_size = 100
            self.enabled = False
            self.global_rate_limit_enabled = False
            self.max_global_requests_per_minute = 60
            self._initialization_error = True

    def _validate_configuration(self) -> bool:
        is_valid = True
        if self.max_requests_per_period <= 0:
            logger.error("Invalid MAX_FILES_PER_PERIOD: must be > 0.")
            is_valid = False
        if self.rate_limit_period_seconds <= 0:
            logger.error("Invalid RATE_LIMIT_PERIOD_MINUTES: must be > 0.")
            is_valid = False
        if self.max_queue_size <= 0:
            logger.error("Invalid MAX_QUEUE_SIZE: must be > 0.")
            is_valid = False
        if self.global_rate_limit_enabled and self.max_global_requests_per_minute <= 0:
            logger.error("Invalid MAX_GLOBAL_REQUESTS_PER_MINUTE: must be > 0 when global rate limit is enabled.")
            is_valid = False
        return is_valid

    def is_owner(self, user_id: int) -> bool:
        return user_id == Var.OWNER_ID

    async def is_authorized_user(self, user_id: int) -> bool:
        current_time = time.time()
        if user_id in self.auth_cache:
            is_auth, timestamp = self.auth_cache[user_id]
            if current_time - timestamp < self.auth_cache_ttl_seconds:
                return is_auth

        try:
            authorized_user = await db.authorized_users_col.find_one({"user_id": user_id})
            is_auth = bool(authorized_user)
            self.auth_cache[user_id] = (is_auth, current_time)
            return is_auth
        except Exception as e:
            logger.error(f"Database error checking authorized user {user_id}: {e}")
            return False

    async def get_user_priority(self, user_id: int) -> str:
        if self.is_owner(user_id):
            return 'owner'
        if await self.is_authorized_user(user_id):
            return 'authorized'
        return 'regular'

    async def check_limits(self, user_id: int, record: bool = True) -> bool:
        if not self.enabled or self._initialization_error or self.is_owner(user_id):
            return True

        current_time = time.time()

        if self.global_rate_limit_enabled:
            while self.global_requests and self.global_requests[0] <= current_time - 60:
                self.global_requests.popleft()
            if len(self.global_requests) >= self.max_global_requests_per_minute:
                return False

        user_timestamps = self.user_requests.setdefault(user_id, deque())
        while user_timestamps and user_timestamps[0] <= current_time - self.rate_limit_period_seconds:
            user_timestamps.popleft()
        if len(user_timestamps) >= self.max_requests_per_period:
            return False

        if record:
            if self.global_rate_limit_enabled:
                self.global_requests.append(current_time)
            user_timestamps.append(current_time)
        return True

    async def _requeue_request(self, request_data: dict, queue_type: str):
        async with self.request_lock:
            if queue_type == "priority":
                self.priority_queue.appendleft(request_data)
            else:
                self.request_queue.appendleft(request_data)
            self.request_event.set()
        logger.debug(f"Re-queued request for user {request_data['user_id']} to {queue_type} queue.")

    async def add_to_queue(self, func: Callable, user_id: int, file_identifier: Optional[str] = None, *args, **kwargs):
        if not self.enabled:
            await func(*args, **kwargs)
            return

        request_data = {
            'func': func, 'user_id': user_id, 'args': args, 'kwargs': kwargs,
            'timestamp': time.time(), 'user_priority': await self.get_user_priority(user_id),
            'file_identifier': file_identifier
        }

        async with self.request_lock:
            total_queued = len(self.request_queue) + len(self.priority_queue)
            if total_queued >= self.max_queue_size:
                raise QueueFullError("Queue is full")

            if request_data['user_priority'] == 'authorized':
                self.priority_queue.append(request_data)
                queue_name = "priority"
            else:
                self.request_queue.append(request_data)
                queue_name = "regular"

            self.user_queue_counts[user_id] = self.user_queue_counts.get(user_id, 0) + 1
            logger.debug(f"Added request for user {user_id} to {queue_name} queue. Total queued: {total_queued + 1}")
            self.request_event.set()

    async def request_executor(self):
        logger.debug("Request executor started.")
        while True:
            try:
                await self.request_event.wait()

                async with self.request_lock:
                    queue, queue_type = (self.priority_queue, "priority") if self.priority_queue else (self.request_queue, "regular")
                    if not queue:
                        self.request_event.clear()
                        continue
                    request_data = queue.popleft()

                user_id = request_data['user_id']
                processed = False
                if not self.is_owner(user_id):
                    if not await self.check_limits(user_id, record=True):
                        await self._requeue_request(request_data, queue_type)
                        await asyncio.sleep(0.5)
                        continue

                logger.debug(f"Processing request for user {user_id} from {queue_type} queue.")
                start_time = time.time()
                try:
                    await request_data['func'](*request_data['args'], **request_data['kwargs'])
                    processing_time = time.time() - start_time
                    self.processing_times.append(processing_time)
                    if self.processing_times:
                        self.average_processing_time = sum(self.processing_times) / len(self.processing_times)

                    file_identifier = request_data.get('file_identifier')
                    if file_identifier:
                        file_times = self.file_processing_times.setdefault(file_identifier, deque(maxlen=100))
                        file_times.append(processing_time)

                    processed = True

                except FloodWait as e:
                    logger.warning(f"FloodWait for user {user_id}, waiting {e.value}s before re-queuing.")
                    await asyncio.sleep(e.value)
                    await self._requeue_request(request_data, queue_type)
                except Exception as e:
                    logger.error(f"Error processing queued request for user {user_id}: {e}", exc_info=True)
                    processed = True
                finally:
                    async with self.request_lock:
                        if processed and user_id in self.user_queue_counts:
                            self.user_queue_counts[user_id] -= 1
                            if self.user_queue_counts[user_id] <= 0:
                                self.user_queue_counts.pop(user_id, None)

            except asyncio.CancelledError:
                logger.debug("Request executor cancelled, shutting down.")
                break
            except Exception as e:
                logger.critical(f"Critical error in request executor: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def shutdown(self):
        logger.debug("Shutting down rate limiter and clearing queues...")
        async with self.request_lock:
            self.request_queue.clear()
            self.priority_queue.clear()
            self.user_queue_counts.clear()
            self.request_event.clear()
        logger.debug("Rate limiter queues cleared.")

    def get_queue_status(self) -> dict:
        return {
            'regular_queue_size': len(self.request_queue),
            'priority_queue_size': len(self.priority_queue),
            'total_queued': len(self.request_queue) + len(self.priority_queue),
            'max_queue_size': self.max_queue_size,
            'active_users_in_queue': len(self.user_queue_counts),
            'enabled': self.enabled,
        }

    async def get_user_queue_position(self, user_id: int) -> dict:
        user_priority = await self.get_user_priority(user_id)
        position = -1
        queue_to_search = self.priority_queue if user_priority == 'authorized' else self.request_queue
        
        for idx, req in enumerate(queue_to_search):
            if req.get('user_id') == user_id:
                position = idx + 1
                break

        effective_position = position
        if user_priority == 'regular' and position > -1:
            effective_position += len(self.priority_queue)

        return {
            'user_priority': user_priority,
            'position_in_own_queue': position if position > -1 else None,
            'effective_position': effective_position if effective_position > -1 else None,
            'priority_queue_size': len(self.priority_queue),
            'regular_queue_size': len(self.request_queue),
            'bypasses_rate_limit': user_priority == 'owner'
        }

    def _get_base_processing_time(self, file_identifier: Optional[str]) -> float:
        if file_identifier and file_identifier in self.file_processing_times:
            file_times = self.file_processing_times[file_identifier]
            if file_times:
                return sum(file_times) / len(file_times)
        return self.average_processing_time

    async def _calculate_queue_wait(self, user_id: int, effective_processing_time: float) -> float:
        pos_info = await self.get_user_queue_position(user_id)
        items_ahead = (pos_info['effective_position'] - 1) if pos_info['effective_position'] else 0
        return items_ahead * effective_processing_time

    def _calculate_user_rate_limit_wait(self, user_id: int, future_time: float) -> float:
        user_timestamps = self.user_requests.get(user_id, deque())
        future_user_timestamps = deque(ts for ts in user_timestamps if ts > future_time - self.rate_limit_period_seconds)

        if len(future_user_timestamps) >= self.max_requests_per_period:
            reset_time = future_user_timestamps[0] + self.rate_limit_period_seconds
            return max(0.0, reset_time - future_time)
        return 0.0

    def _calculate_global_rate_limit_wait(self, future_time: float) -> float:
        if not self.global_rate_limit_enabled:
            return 0.0

        future_global_requests = deque(ts for ts in self.global_requests if ts > future_time - 60)
        
        if len(future_global_requests) >= self.max_global_requests_per_minute:
            oldest_request_time = future_global_requests[0]
            reset_time = oldest_request_time + 60
            return max(0.0, reset_time - future_time)
        return 0.0

    async def estimate_wait_time(self, user_id: int, file_identifier: Optional[str] = None) -> float:
        if self.is_owner(user_id):
            return 0.0

        base_processing_time = self._get_base_processing_time(file_identifier)
        min_time_per_request = self.rate_limit_period_seconds / self.max_requests_per_period if self.max_requests_per_period > 0 else 0
        effective_processing_time = max(base_processing_time, min_time_per_request)
        
        if self.global_rate_limit_enabled and self.max_global_requests_per_minute > 0:
            min_time_per_global = 60 / self.max_global_requests_per_minute
            effective_processing_time = max(effective_processing_time, min_time_per_global)

        queue_wait = await self._calculate_queue_wait(user_id, effective_processing_time)
        future_time = time.time() + queue_wait

        rate_limit_wait = self._calculate_user_rate_limit_wait(user_id, future_time)
        global_wait = self._calculate_global_rate_limit_wait(future_time)

        return queue_wait + rate_limit_wait + global_wait


rate_limiter = RateLimiter()


async def request_executor():
    await rate_limiter.request_executor()


async def handle_rate_limited_request(bot: Client, message: Message, handler: Callable, *args, **kwargs):
    rl_user_id = kwargs.pop('rl_user_id', None)
    user_id = rl_user_id if rl_user_id is not None else (message.from_user.id if message and message.from_user else None)
    if not isinstance(user_id, int):
        logger.error(f"Invalid user_id provided for rate limiting: {user_id}")
        return

    file_identifier = message.document.file_unique_id if message and message.document else None

    if rate_limiter.is_owner(user_id):
        logger.debug(f"Owner {user_id} bypassing rate limit.")
        await handler(bot, message, *args, **kwargs)
        return

    if await rate_limiter.check_limits(user_id, record=True):
        logger.debug(f"User {user_id} within rate limits, executing immediately.")
        await handler(bot, message, *args, **kwargs)
        return

    is_channel = rl_user_id is not None and rl_user_id < 0

    if not is_channel:
        try:
            user_priority = await rate_limiter.get_user_priority(user_id)
            notification_msg = await send_queue_notification(
                bot, message, is_priority=(user_priority == 'authorized'), file_identifier=file_identifier
            )
            kwargs['notification_msg'] = notification_msg
        except Exception as e:
            logger.error(f"Error sending queue notification for user {user_id}: {e}", exc_info=True)

    try:
        await rate_limiter.add_to_queue(handler, user_id, file_identifier, bot, message, *args, **kwargs)
        logger.debug(f"Request for user {user_id} queued.")
    except QueueFullError:
        logger.warning(f"Queue full, request for user {user_id} rejected.")
        if not is_channel:
            await send_queue_full_message(bot, message, file_identifier)
    except Exception as e:
        logger.error(f"Error adding request to queue for user {user_id}: {e}", exc_info=True)
        if not is_channel:
            await send_queue_full_message(bot, message, file_identifier)


async def _send_notification(bot: Client, message: Message, template: str, file_identifier: Optional[str], **format_kwargs):
    try:
        if message.from_user:
            user_id = message.from_user.id
            wait_seconds = await rate_limiter.estimate_wait_time(user_id, file_identifier)
            wait_estimate = max(1, math.ceil(wait_seconds / 60))

            text = template.format(wait_estimate=wait_estimate, s="s" if wait_estimate > 1 else "", **format_kwargs)

            try:
                return await bot.send_message(
                    chat_id=message.chat.id,
                    text=text,
                    reply_to_message_id=message.id
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                return await bot.send_message(
                    chat_id=message.chat.id,
                    text=text,
                    reply_to_message_id=message.id
                )
        else:
            logger.debug("Skipping notification for channel message (no from_user)")
            return None
    except (FloodWait, RPCError) as e:
        user_id = message.from_user.id if message.from_user else "channel"
        logger.warning(f"Error sending notification to user {user_id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending notification: {e}", exc_info=True)
    return None


async def send_queue_notification(bot: Client, message: Message, is_priority: bool, file_identifier: Optional[str]):
    if is_priority:
        template = MSG_RATE_LIMIT_QUEUE_PRIORITY
        params = {}
    else:
        template = MSG_RATE_LIMIT_QUEUE_REGULAR
        time_window = rate_limiter.rate_limit_period_seconds // 60
        params = {
            "max_requests": rate_limiter.max_requests_per_period,
            "time_window": time_window,
            "s1": "s" if rate_limiter.max_requests_per_period > 1 else "",
            "s2": "s" if time_window > 1 else ""
        }
    user_id = message.from_user.id if message.from_user else "channel"
    logger.debug(f"Sending {'priority' if is_priority else 'regular'} queue notification to user {user_id}")
    return await _send_notification(bot, message, template, file_identifier, **params)


async def send_queue_full_message(bot: Client, message: Message, file_identifier: Optional[str]):
    user_id = message.from_user.id if message.from_user else "channel"
    logger.debug(f"Sending queue full message to user {user_id}")
    await _send_notification(bot, message, MSG_RATE_LIMIT_QUEUE_FULL, file_identifier)
