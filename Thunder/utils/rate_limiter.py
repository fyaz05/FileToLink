# Thunder/utils/rate_limiter.py

import time
import asyncio
from collections import deque
from typing import Callable, Dict, Optional
from pyrogram import Client
from pyrogram.types import Message, ReplyParameters
from pyrogram.errors import FloodWait, RPCError
from Thunder.utils.logger import logger
from Thunder.utils.database import db
from Thunder.utils.messages import (
    MSG_RATE_LIMIT_QUEUE_PRIORITY,
    MSG_RATE_LIMIT_QUEUE_REGULAR,
    MSG_RATE_LIMIT_QUEUE_FULL,
    MSG_RATE_LIMIT_QUEUE_STATUS
)
from Thunder.utils.handler import handle_flood_wait
from Thunder.vars import Var

class RateLimiterError(Exception):
    pass

class QueueFullError(RateLimiterError):
    pass

class RateLimiter:
    def __init__(self):
        self.user_requests: Dict[int, deque] = {}
        self.request_queue: deque = deque()
        self.priority_queue: deque = deque()
        self.request_event: asyncio.Event = asyncio.Event()
        self.request_lock: asyncio.Lock = asyncio.Lock()
        self.user_batch_counters: Dict[int, int] = {}
        self.user_last_request_time: Dict[int, float] = {}
        self.global_requests: deque = deque()
        self._initialization_error = False

        try:
            self.max_requests_per_period = Var.MAX_FILES_PER_PERIOD
            self.rate_limit_period_seconds = Var.RATE_LIMIT_PERIOD_MINUTES * 60
            self.max_queue_size = Var.MAX_QUEUE_SIZE
            self.enabled = Var.RATE_LIMIT_ENABLED
            self.global_rate_limit_enabled = Var.GLOBAL_RATE_LIMIT
            self.max_global_requests_per_minute = Var.MAX_GLOBAL_REQUESTS_PER_MINUTE

            if not self._validate_configuration():
                logger.warning("Rate limiter disabled due to invalid configuration")
                self.enabled = False
            else:
                logger.info(f"Rate limiter initialized: enabled={self.enabled}, "
                            f"max_requests_per_period={self.max_requests_per_period}, "
                            f"period={self.rate_limit_period_seconds}s, "
                            f"max_queue_size={self.max_queue_size}, "
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
        if self.max_requests_per_period <= 0:
            logger.error(f"Invalid MAX_FILES_PER_PERIOD: {self.max_requests_per_period}, must be > 0.")
            return False
        if self.rate_limit_period_seconds <= 0:
            logger.error(f"Invalid RATE_LIMIT_PERIOD_MINUTES: {self.rate_limit_period_seconds/60}, must be > 0.")
            return False
        if self.max_queue_size <= 0:
            logger.error(f"Invalid MAX_QUEUE_SIZE: {self.max_queue_size}, must be > 0.")
            return False
        if self.global_rate_limit_enabled and self.max_global_requests_per_minute <= 0:
            logger.error(f"Invalid MAX_GLOBAL_REQUESTS_PER_MINUTE: {self.max_global_requests_per_minute}, must be > 0 when global rate limit is enabled.")
            return False
        return True

    def is_owner(self, user_id: int) -> bool:
        return user_id == Var.OWNER_ID

    async def is_authorized_user(self, user_id: int) -> bool:
        try:
            authorized_user = await db.authorized_users_col.find_one({"user_id": user_id})
            return bool(authorized_user)
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
        if not self.enabled or self._initialization_error:
            return True

        if self.is_owner(user_id):
            return True

        current_time = time.time()

        user_timestamps = self.user_requests.setdefault(user_id, deque())
        while user_timestamps and user_timestamps[0] <= current_time - self.rate_limit_period_seconds:
            user_timestamps.popleft()

        # If user has no recent requests (first request), bypass global rate limits
        if len(user_timestamps) == 0:
            if record:
                self.global_requests.append(current_time)
                user_timestamps.append(current_time)
            return True

        # User has recent requests, apply global limits if enabled
        if self.global_rate_limit_enabled:
            while self.global_requests and self.global_requests[0] <= current_time - 60:  # Fixed boundary to <=
                self.global_requests.popleft()
            if len(self.global_requests) >= self.max_global_requests_per_minute:
                return False

        # Check user-specific limits
        if len(user_timestamps) >= self.max_requests_per_period:
            return False  # User is rate-limited

        if record:
            self.global_requests.append(current_time)
            user_timestamps.append(current_time)
        return True

    async def add_to_queue(self, func: Callable, user_id: int, *args, **kwargs) -> bool:
        if not self.enabled:
            await func(*args, **kwargs)
            return True

        request_data = {
            'func': func,
            'user_id': user_id,
            'args': args,
            'kwargs': kwargs,
            'timestamp': time.time(),
            'user_priority': await self.get_user_priority(user_id)
        }

        async with self.request_lock:
            total_queued = len(self.request_queue) + len(self.priority_queue)
            if total_queued >= self.max_queue_size:
                raise QueueFullError("Queue is full")

            if request_data['user_priority'] == 'authorized':
                self.priority_queue.append(request_data)
                logger.debug(f"Added request for user {user_id} to priority queue. Total queued: {total_queued + 1}")
            else:
                self.request_queue.append(request_data)
                logger.debug(f"Added request for user {user_id} to regular queue. Total queued: {total_queued + 1}")
            self.request_event.set()
        return True

    async def request_executor(self):
        logger.info("Request executor started.")
        while True:
            try:
                await self.request_event.wait()

                async with self.request_lock:
                    request_data = None
                    queue_type = None
                    if self.priority_queue:
                        request_data = self.priority_queue.popleft()
                        queue_type = "priority"
                    elif self.request_queue:
                        request_data = self.request_queue.popleft()
                        queue_type = "regular"
                    else:
                        self.request_event.clear()
                        continue

                user_id = request_data['user_id']
                func = request_data['func']
                args = request_data['args']
                kwargs = request_data['kwargs']

                if not self.is_owner(user_id):
                    current_time = time.time()
                    if not await self.check_limits(user_id, record=False):
                        async with self.request_lock:
                            if queue_type == "priority":
                                self.priority_queue.appendleft(request_data)
                            else:
                                self.request_queue.appendleft(request_data)
                            self.request_event.set()
                        logger.debug(f"Re-queued request for user {user_id} due to re-evaluation of limits.")
                        continue
                    else:
                        # Record the request since check passed without recording
                        self.global_requests.append(current_time)
                        user_timestamps = self.user_requests.get(user_id, deque())
                        user_timestamps.append(current_time)

                logger.debug(f"Processing request for user {user_id} from {queue_type} queue.")
                try:
                    await func(*args, **kwargs)
                except FloodWait as e:
                    logger.warning(f"FloodWait for user {user_id}, waiting {e.value}s before re-queuing.")
                    await asyncio.sleep(e.value)
                    async with self.request_lock:
                        if queue_type == "priority":
                            self.priority_queue.append(request_data)
                        else:
                            self.request_queue.append(request_data)
                        self.request_event.set()
                except Exception as e:
                    logger.error(f"Error processing queued request for user {user_id}: {e}", exc_info=True)

            except asyncio.CancelledError:
                logger.info("Request executor cancelled, shutting down.")
                break
            except Exception as e:
                logger.critical(f"Critical error in request executor: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def shutdown(self) -> None:
        logger.info("Shutting down rate limiter and clearing queues...")
        async with self.request_lock:
            self.request_queue.clear()
            self.priority_queue.clear()
            self.request_event.clear()
        logger.info("Rate limiter queues cleared.")

    def get_queue_status(self) -> dict:
        return {
            'regular_queue_size': len(self.request_queue),
            'priority_queue_size': len(self.priority_queue),
            'total_queued': len(self.request_queue) + len(self.priority_queue),
            'max_queue_size': self.max_queue_size,
            'enabled': self.enabled,
            'active_users': len(self.user_requests),
            'max_requests_per_period': self.max_requests_per_period,
            'time_window_seconds': self.rate_limit_period_seconds,
            'global_rate_limit_enabled': self.global_rate_limit_enabled,
            'max_global_requests_per_minute': self.max_global_requests_per_minute,
            'current_global_requests': len(self.global_requests)
        }

    def reset_user_limits(self, user_id: Optional[int] = None) -> None:
        if user_id is not None:
            self.user_requests.pop(user_id, None)
            self.user_batch_counters.pop(user_id, None)
            self.user_last_request_time.pop(user_id, None)
            logger.debug(f"Reset rate limits for user {user_id}")
        else:
            self.user_requests.clear()
            self.user_batch_counters.clear()
            self.user_last_request_time.clear()
            self.global_requests.clear()
            logger.debug("Reset rate limits for all users")

    async def get_user_queue_position(self, user_id: int) -> dict:
        user_priority = await self.get_user_priority(user_id)
        priority_position = None
        regular_position = None

        if user_priority == 'authorized':
            queue_copy = list(self.priority_queue)
            for idx, req in enumerate(queue_copy):
                if req.get('user_id') == user_id:
                    priority_position = idx + 1
                    break

        if user_priority == 'regular':
            queue_copy = list(self.request_queue)
            for idx, req in enumerate(queue_copy):
                if req.get('user_id') == user_id:
                    regular_position = idx + 1
                    break

        return {
            'user_priority': user_priority,
            'priority_queue_position': priority_position,
            'regular_queue_position': regular_position,
            'priority_queue_size': len(self.priority_queue),
            'regular_queue_size': len(self.request_queue),
            'bypasses_rate_limit': user_priority == 'owner'
        }

rate_limiter = RateLimiter()

async def request_executor() -> None:
    await rate_limiter.request_executor()

async def handle_rate_limited_request(bot: Client, message: Message, handler: Callable, rl_user_id: Optional[int] = None, *args, **kwargs) -> bool:
    try:
        user_id = rl_user_id if rl_user_id is not None else (message.from_user.id if message and message.from_user else None)
        if user_id is None:
            logger.error("Cannot handle rate limited request without user information (rl_user_id or message.from_user.id)")
            return False

        if rate_limiter.is_owner(user_id):
            logger.debug(f"Owner {user_id} bypassing rate limit and executing immediately.")
            await handler(bot, message, *args, **kwargs)
            return True

        can_proceed = await rate_limiter.check_limits(user_id)
        if can_proceed:
            logger.debug(f"User {user_id} within rate limits, executing immediately.")
            await handler(bot, message, *args, **kwargs)
            return True
        else:
            try:
                notification_msg = await send_queue_notification(bot, message, priority=(await rate_limiter.get_user_priority(user_id) == 'authorized'))
                kwargs['notification_msg'] = notification_msg
                await rate_limiter.add_to_queue(handler, user_id, bot, message, *args, **kwargs)
                logger.debug(f"Request for user {user_id} queued.")
                return True
            except QueueFullError:
                await send_queue_full_message(bot, message)
                logger.warning(f"Queue full, request for user {user_id} rejected.")
                return False
            except Exception as e:
                logger.error(f"Error adding request to queue for user {user_id}: {e}", exc_info=True)
                await send_queue_full_message(bot, message)
                return False
    except Exception as e:
        logger.critical(f"Critical error in handle_rate_limited_request for user {user_id}: {e}", exc_info=True)
        return False

async def send_queue_notification(bot: Client, message: Message, priority: bool = False):
    try:
        user_id = message.from_user.id if message and message.from_user else None

        if user_id is not None:
            current_time = time.time()
            last_request_time = rate_limiter.user_last_request_time.get(user_id, 0)
            
            if current_time - last_request_time < 5.0:
                rate_limiter.user_batch_counters[user_id] = rate_limiter.user_batch_counters.get(user_id, 0) + 1
            else:
                rate_limiter.user_batch_counters[user_id] = 1
            
            rate_limiter.user_last_request_time[user_id] = current_time
            batch_offset = rate_limiter.user_batch_counters[user_id] - 1
            
            queue_pos = await rate_limiter.get_user_queue_position(user_id)
            if priority:
                position = queue_pos.get('priority_queue_position') or 1
            else:
                position = queue_pos.get('regular_queue_position') or 1
            wait_estimate = position + batch_offset
        else:
            batch_offset = 0
            position = 1
            wait_estimate = 1
        time_window = rate_limiter.rate_limit_period_seconds // 60
        max_requests = rate_limiter.max_requests_per_period

        s = "s" if wait_estimate > 1 else ""
        s1 = "s" if wait_estimate > 1 else ""
        s2 = "s" if time_window > 1 else ""

        if priority:
            queue_message = MSG_RATE_LIMIT_QUEUE_PRIORITY.format(
                wait_estimate=wait_estimate,
                s=s
            )
        else:
            queue_message = MSG_RATE_LIMIT_QUEUE_REGULAR.format(
                wait_estimate=wait_estimate,
                max_requests=max_requests,
                time_window=time_window,
                s1=s1,
                s2=s2
            )
        
        sent_msg = await handle_flood_wait(
            bot.send_message,
            chat_id=message.chat.id,
            text=queue_message,
            reply_parameters=ReplyParameters(message_id=message.id)
        )
        logger.debug(f"Sent {'priority' if priority else 'regular'} queue notification to {'user' if user_id else 'channel'} {user_id or message.chat.id}")
        return sent_msg
    except FloodWait as e:
        logger.warning(f"FloodWait sending queue notification to user {user_id}: {e}")
    except RPCError as e:
        logger.error(f"RPC error sending queue notification to user {user_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending queue notification to user {user_id}: {e}", exc_info=True)
    return None

async def send_queue_full_message(bot: Client, message: Message) -> None:
    try:
        user_id = message.from_user.id if message and message.from_user else None

        status = rate_limiter.get_queue_status()
        wait_estimate = max(5, min(30, status['total_queued'] // 5))
        s = "s" if wait_estimate > 1 else ""
        error_message = MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=wait_estimate, s=s)
        
        await bot.send_message(
            chat_id=message.chat.id,
            text=error_message,
            reply_parameters=ReplyParameters(message_id=message.id)
        )
        logger.debug(f"Sent queue full message to {'user' if user_id else 'channel'} {user_id or message.chat.id}")
    except FloodWait as e:
        logger.warning(f"FloodWait sending queue full message to user {user_id}: {e}")
    except RPCError as e:
        logger.error(f"RPC error sending queue full message to user {user_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending queue full message to user {user_id}: {e}", exc_info=True)

async def send_queue_status_message(bot: Client, message: Message) -> None:
    try:
        user_id = message.from_user.id if message and message.from_user else None

        status = rate_limiter.get_queue_status()
        enabled_status = "Enabled" if status.get('enabled') else "Disabled"
        global_enabled_status = "Enabled" if status.get('global_rate_limit_enabled') else "Disabled"

        status_text = MSG_RATE_LIMIT_QUEUE_STATUS.format(
            regular_queue_size=status['regular_queue_size'],
            priority_queue_size=status['priority_queue_size'],
            total_queued=status['total_queued'],
            max_queue_size=status['max_queue_size'],
            enabled_status=enabled_status,
            active_users=status['active_users'],
            max_requests_per_period=status['max_requests_per_period'],
            time_window_seconds=status['time_window_seconds'],
            global_enabled_status=global_enabled_status,
            max_global_requests_per_minute=status['max_global_requests_per_minute'],
            current_global_requests=status['current_global_requests']
        )
        
        await bot.send_message(
            chat_id=message.chat.id,
            text=status_text,
            reply_parameters=ReplyParameters(message_id=message.id)
        )
        logger.debug(f"Sent queue status to {'user' if user_id else 'channel'} {user_id or message.chat.id}")
    except FloodWait as e:
        logger.warning(f"FloodWait sending queue status to user {user_id}: {e}")
    except RPCError as e:
        logger.error(f"RPC error sending queue status to user {user_id}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error sending queue status to user {user_id}: {e}", exc_info=True)