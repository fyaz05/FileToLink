# Thunder/utils/rate_limiter.py

import os
import time
import asyncio
from typing import Dict, List, Optional
from pyrogram import Client
from pyrogram.types import Message, ReplyParameters
from pyrogram.errors import FloodWait, RPCError
from Thunder.utils.logger import logger
from Thunder.utils.database import db
from Thunder.utils.messages import (
    MSG_RATE_LIMIT_QUEUE_PRIORITY,
    MSG_RATE_LIMIT_QUEUE_REGULAR,
    MSG_RATE_LIMIT_QUEUE_FULL,
    MSG_RATE_LIMIT_QUEUE_STATUS,
    MSG_RATE_LIMIT_QUEUE_STATUS_ERROR
)
from Thunder.utils.handler import handle_flood_wait
from Thunder.vars import Var

class RateLimiterError(Exception):
    pass

class QueueFullError(RateLimiterError):
    pass

class ConfigurationError(RateLimiterError):
    pass

class DatabaseError(RateLimiterError):
    pass

class ProcessingError(RateLimiterError):
    pass

class RateLimiter:
    def __init__(self):
        try:
            self.user_requests: Dict[int, List[float]] = {}
            self.request_queue: asyncio.Queue = asyncio.Queue()
            self.priority_queue: asyncio.Queue = asyncio.Queue()
            self.queue_processor_task: Optional[asyncio.Task] = None
            self._initialization_error = False
            self._last_cleanup_time = time.time()
            self._cleanup_interval = 300
            self.user_batch_counters: Dict[int, int] = {}
            self.user_last_request_time: Dict[int, float] = {}
            self.global_requests: List[float] = []
            try:
                self.max_requests = Var.MAX_FILES_PER_PERIOD
                self.time_window = Var.RATE_LIMIT_PERIOD_MINUTES * 60
                self.max_queue_size = Var.MAX_QUEUE_SIZE
                self.enabled = Var.RATE_LIMIT_ENABLED
                self.global_enabled = Var.GLOBAL_RATE_LIMIT
                self.max_global_requests = Var.MAX_GLOBAL_REQUESTS_PER_MINUTE
                if not self._validate_configuration():
                    logger.warning("Rate limiter disabled due to invalid configuration")
                else:
                    logger.info(f"Rate limiter initialized successfully: max_requests={self.max_requests}, "
                                f"time_window={self.time_window}s, max_queue_size={self.max_queue_size}, "
                                f"enabled={self.enabled}, global_enabled={self.global_enabled}, "
                                f"max_global_requests={self.max_global_requests}")
            except Exception as config_error:
                logger.error(f"Rate limiter configuration error: {config_error}")
                logger.debug(f"Configuration error details: {type(config_error).__name__}: {config_error}")
                self.max_requests = 5
                self.time_window = 60
                self.max_queue_size = 100
                self.enabled = False
                self.global_enabled = False
                self.max_global_requests = 60
                self._initialization_error = True
                logger.warning("Rate limiter using safe defaults due to configuration error (graceful degradation)")
                logger.info(f"Safe defaults applied: max_requests={self.max_requests}, "
                            f"time_window={self.time_window}s, max_queue_size={self.max_queue_size}, "
                            f"enabled={self.enabled}, global_enabled={self.global_enabled}, "
                            f"max_global_requests={self.max_global_requests}")
        except Exception as init_error:
            logger.critical(f"Critical error initializing rate limiter: {init_error}")
            self.user_requests = {}
            self.request_queue = asyncio.Queue()
            self.priority_queue = asyncio.Queue()
            self.queue_processor_task = None
            self.max_requests = 5
            self.time_window = 60
            self.max_queue_size = 100
            self.enabled = False
            self._initialization_error = True
            raise RateLimiterError(f"Failed to initialize rate limiter: {init_error}")

    def _cleanup_expired_requests(self, user_id: int) -> None:
        try:
            current_time = time.time()
            if user_id in self.user_requests:
                original_count = len(self.user_requests[user_id])
                self.user_requests[user_id] = [
                    timestamp for timestamp in self.user_requests[user_id]
                    if current_time - timestamp < self.time_window
                ]
                cleaned_count = len(self.user_requests[user_id])
                if not self.user_requests[user_id]:
                    del self.user_requests[user_id]
                    logger.debug(f"Cleaned up all expired requests for user {user_id}")
                elif original_count != cleaned_count:
                    logger.debug(f"Cleaned up {original_count - cleaned_count} expired requests for user {user_id}")
        except Exception as cleanup_error:
            logger.error(f"Error cleaning up expired requests for user {user_id}: {cleanup_error}")
            try:
                if user_id in self.user_requests:
                    del self.user_requests[user_id]
                    logger.warning(f"Removed all requests for user {user_id} due to cleanup error")
            except Exception as fallback_error:
                logger.error(f"Failed to remove requests for user {user_id}: {fallback_error}")

    def _validate_configuration(self) -> bool:
        if self.max_requests <= 0:
            logger.error(f"Invalid MAX_FILES_PER_PERIOD: {self.max_requests}, must be > 0. Rate limiting disabled.")
            self.enabled = False
            return False
        if self.time_window <= 0:
            logger.error(f"Invalid RATE_LIMIT_PERIOD_MINUTES: {self.time_window/60}, must be > 0. Rate limiting disabled.")
            self.enabled = False
            return False
        if self.max_queue_size <= 0:
            logger.error(f"Invalid MAX_QUEUE_SIZE: {self.max_queue_size}, must be > 0. Rate limiting disabled.")
            self.enabled = False
            return False
        return True

    def _cleanup_inactive_users(self) -> None:
        try:
            current_time = time.time()
            inactive_threshold = self.time_window * 2
            before_count = len(self.user_requests)
            removed_count = 0
            inactive_users = []
            for user_id, timestamps in self.user_requests.items():
                if not timestamps or (current_time - max(timestamps) > inactive_threshold):
                    inactive_users.append(user_id)
                    removed_count += 1
            for user_id in inactive_users:
                del self.user_requests[user_id]
                self.user_batch_counters.pop(user_id, None)
                self.user_last_request_time.pop(user_id, None)
            if removed_count > 0:
                logger.debug(f"Memory cleanup: removed {removed_count}/{before_count} inactive users")
            self._last_cleanup_time = current_time
        except Exception as cleanup_error:
            logger.error(f"Error during inactive users cleanup: {cleanup_error}")

    async def check_rate_limit(self, user_id: int, record: bool = True, is_batch_process: bool = False) -> bool:
        try:
            if user_id is None or not isinstance(user_id, int) or user_id <= 0:
                logger.error(f"Invalid user_id for rate limit check: {user_id}")
                return True
            if not self.enabled or self._initialization_error:
                logger.debug(f"Rate limiting disabled or has errors, allowing user {user_id}")
                return True
            if self.is_owner(user_id):
                logger.debug(f"Owner {user_id} bypassing rate limit check")
                return True
            
            current_time = time.time()

            if self.global_enabled:
                self.global_requests = [
                    timestamp for timestamp in self.global_requests
                    if current_time - timestamp < 60
                ]
                current_global_requests = len(self.global_requests)
                logger.debug(f"Global requests: {current_global_requests}/{self.max_global_requests}")

                if current_global_requests >= self.max_global_requests:
                    logger.debug(f"Global rate limit exceeded ({current_global_requests}/{self.max_global_requests}), denying user {user_id}")
                    return False

            if not is_batch_process:
                self._cleanup_expired_requests(user_id)
                current_requests = len(self.user_requests.get(user_id, []))
                logger.debug(f"User {user_id} has {current_requests}/{self.max_requests} requests in current window")
                if current_requests >= self.max_requests:
                    logger.debug(f"User {user_id} exceeded per-user rate limit ({current_requests}/{self.max_requests})")
                    return False

            if record:
                if not is_batch_process:
                    if user_id not in self.user_requests:
                        self.user_requests[user_id] = []
                    self.user_requests[user_id].append(current_time)
                    logger.debug(f"User {user_id} within rate limit, request recorded.")
                
                if self.global_enabled:
                    self.global_requests.append(current_time)
                    logger.debug(f"Global request recorded.")
            
            return True
        except Exception as rate_check_error:
            logger.error(f"Error checking rate limit for user {user_id}: {rate_check_error}", exc_info=True)
            logger.warning(f"Rate limit check failed for user {user_id}, allowing request to proceed (graceful degradation)")
            return True

    async def add_to_queue(self, request_data: dict, priority: bool = False) -> bool:
        try:
            if not isinstance(request_data, dict):
                raise ValueError(f"request_data must be a dictionary, got {type(request_data)}")
            user_id = request_data.get('user_id')
            if not user_id:
                raise ValueError("request_data missing required 'user_id' field")
            target_queue = self.priority_queue if priority else self.request_queue
            total_queued = self.request_queue.qsize() + self.priority_queue.qsize()
            logger.debug(f"Attempting to queue request for user {user_id}: "
                         f"priority={priority}, total_queued={total_queued}/{self.max_queue_size}")
            if total_queued >= self.max_queue_size:
                logger.warning(f"Queue full ({total_queued}/{self.max_queue_size}), cannot queue request for user {user_id}")
                raise QueueFullError(f"Queue is full ({total_queued}/{self.max_queue_size})")
            try:
                target_queue.put_nowait(request_data)
                queue_type = 'priority' if priority else 'regular'
                logger.debug(f"Successfully added request to {queue_type} queue for user {user_id} "
                             f"(queue size: {target_queue.qsize()}, total: {total_queued + 1})")
                return True
            except asyncio.QueueFull:
                logger.error(f"Queue unexpectedly full when adding request for user {user_id}")
                raise QueueFullError("Queue is full")
        except (QueueFullError, ValueError) as expected_error:
            raise
        except Exception as queue_error:
            logger.error(f"Unexpected error adding request to queue: {queue_error}")
            raise RateLimiterError(f"Failed to add request to queue: {queue_error}")

    def is_owner(self, user_id: int) -> bool:
        try:
            if user_id is None or not isinstance(user_id, int):
                return False
            return user_id == Var.OWNER_ID
        except Exception as owner_check_error:
            logger.error(f"Error checking if user {user_id} is owner: {owner_check_error}")
            return False

    async def is_authorized_user(self, user_id: int) -> bool:
        try:
            authorized_user = await db.authorized_users_col.find_one({"user_id": user_id})
            is_authorized = bool(authorized_user)
            logger.debug(f"User {user_id} authorization check: {is_authorized}")
            return is_authorized
        except Exception as db_error:
            logger.error(f"Database error checking authorized user {user_id}: {db_error}")
            logger.warning(f"Treating user {user_id} as regular user due to database error (graceful degradation)")
            return False

    async def get_user_priority(self, user_id: int) -> str:
        try:
            if self.is_owner(user_id):
                logger.debug(f"User {user_id} identified as owner")
                return 'owner'
            try:
                is_authorized = await self.is_authorized_user(user_id)
                if is_authorized:
                    logger.debug(f"User {user_id} identified as authorized")
                    return 'authorized'
                else:
                    logger.debug(f"User {user_id} identified as regular")
                    return 'regular'
            except DatabaseError:
                logger.warning(f"Database error checking user {user_id}, treating as regular user")
                return 'regular'
        except Exception as priority_error:
            logger.error(f"Error determining user priority for {user_id}: {priority_error}")
            return 'regular'

    async def queue_consumer(self):
        logger.info("Queue consumer started")
        while True:
            try:
                if not self.priority_queue.empty():
                    request_data = await self.priority_queue.get()
                    queue_name = "priority"
                elif not self.request_queue.empty():
                    request_data = await self.request_queue.get()
                    queue_name = "regular"
                else:
                    await asyncio.sleep(0.1)
                    continue

                user_id = request_data.get('user_id')
                if not user_id:
                    logger.error(f"Queued request missing user_id, skipping: {request_data}")
                    continue

                if self.is_owner(user_id):
                    logger.debug(f"Processing owner request {user_id} from {queue_name} queue immediately")
                    request_data['kwargs']['skip_rate_limit'] = True
                    yield request_data
                    if queue_name == 'priority':
                        self.priority_queue.task_done()
                    else:
                        self.request_queue.task_done()
                    continue

                can_process = await self.check_rate_limit(user_id, record=True)
                if can_process:
                    logger.debug(f"Rate limit check passed and recorded for user {user_id}. Yielding request from {queue_name} queue.")
                    request_data['kwargs']['skip_rate_limit'] = True
                    yield request_data
                    if queue_name == 'priority':
                        self.priority_queue.task_done()
                    else:
                        self.request_queue.task_done()
                else:
                    logger.debug(f"User {user_id} still rate-limited. Re-queuing request to {queue_name} queue.")
                    if queue_name == 'priority':
                        await self.priority_queue.put(request_data)
                    else:
                        await self.request_queue.put(request_data)
                    
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                logger.info("Queue consumer cancelled, shutting down.")
                break
            except Exception as e:
                logger.error(f"Critical error in queue consumer: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def shutdown(self) -> None:
        logger.info("Shutting down rate limiter and clearing queues...")
        for q in [self.request_queue, self.priority_queue]:
            while not q.empty():
                q.get_nowait()
                q.task_done()
        logger.info("Rate limiter queues cleared.")

    def get_queue_status(self) -> dict:
        return {
            'regular_queue_size': self.request_queue.qsize(),
            'priority_queue_size': self.priority_queue.qsize(),
            'total_queued': self.request_queue.qsize() + self.priority_queue.qsize(),
            'max_queue_size': self.max_queue_size,
            'enabled': self.enabled,
            'active_users': len(self.user_requests),
            'max_requests_per_period': self.max_requests,
            'time_window_seconds': self.time_window
        }

    def get_configuration(self) -> dict:
        return {
            'enabled': self.enabled,
            'max_requests_per_period': self.max_requests,
            'time_window_minutes': self.time_window // 60,
            'time_window_seconds': self.time_window,
            'max_queue_size': self.max_queue_size
        }

    def update_configuration(self, **kwargs) -> bool:
        try:
            updated = False
            if 'enabled' in kwargs:
                old_enabled = self.enabled
                self.enabled = bool(kwargs['enabled'])
                if old_enabled != self.enabled:
                    logger.info(f"Rate limiting {'enabled' if self.enabled else 'disabled'}")
                    updated = True
            if 'max_requests' in kwargs:
                new_max = int(kwargs['max_requests'])
                if new_max > 0:
                    old_max = self.max_requests
                    self.max_requests = new_max
                    if old_max != self.max_requests:
                        logger.info(f"Max requests per period updated: {old_max} -> {self.max_requests}")
                        updated = True
                else:
                    logger.warning(f"Invalid max_requests value: {new_max}, must be > 0")
            if 'time_window_minutes' in kwargs:
                new_window = int(kwargs['time_window_minutes'])
                if new_window > 0:
                    old_window = self.time_window
                    self.time_window = new_window * 60
                    if old_window != self.time_window:
                        logger.info(f"Time window updated: {old_window}s -> {self.time_window}s")
                        updated = True
                else:
                    logger.warning(f"Invalid time_window_minutes value: {new_window}, must be > 0")
            if 'max_queue_size' in kwargs:
                new_size = int(kwargs['max_queue_size'])
                if new_size > 0:
                    old_size = self.max_queue_size
                    self.max_queue_size = new_size
                    if old_size != self.max_queue_size:
                        logger.info(f"Max queue size updated: {old_size} -> {self.max_queue_size}")
                        updated = True
                else:
                    logger.warning(f"Invalid max_queue_size value: {new_size}, must be > 0")
            return updated
        except Exception as e:
            logger.error(f"Error updating rate limiter configuration: {e}")
            return False

    def enable_rate_limiting(self) -> None:
        if not self.enabled:
            self.enabled = True
            logger.info("Rate limiting enabled")

    def disable_rate_limiting(self) -> None:
        if self.enabled:
            self.enabled = False
            logger.info("Rate limiting disabled")

    def is_enabled(self) -> bool:
        return self.enabled

    def reset_user_limits(self, user_id: Optional[int] = None) -> None:
        if user_id is not None:
            if user_id in self.user_requests:
                del self.user_requests[user_id]
            self.user_batch_counters.pop(user_id, None)
            self.user_last_request_time.pop(user_id, None)
            logger.debug(f"Reset rate limits for user {user_id}")
        else:
            self.user_requests.clear()
            self.user_batch_counters.clear()
            self.user_last_request_time.clear()
            logger.debug("Reset rate limits for all users")

    def get_configuration_summary(self) -> str:
        try:
            status = "enabled" if self.enabled else "disabled"
            health_status = "healthy" if not self._initialization_error else "degraded"
            return (
                f"Rate Limiting: {status} ({health_status})\n"
                f"Max requests per period: {self.max_requests}\n"
                f"Time window: {self.time_window // 60} minutes ({self.time_window} seconds)\n"
                f"Max queue size: {self.max_queue_size}\n"
                f"Current active users: {len(self.user_requests)}\n"
                f"Current queue size: {self.request_queue.qsize() + self.priority_queue.qsize()}\n"
                f"Queue processor: {'running' if self.queue_processor_task and not self.queue_processor_task.done() else 'stopped'}"
            )
        except Exception as summary_error:
            logger.error(f"Error generating configuration summary: {summary_error}")
            return f"Error generating summary: {summary_error}"

    def is_healthy(self) -> bool:
        try:
            if self._initialization_error:
                logger.debug("Rate limiter not healthy: initialization error")
                return False
            if self.enabled and (not self.queue_processor_task or self.queue_processor_task.done()):
                logger.debug("Rate limiter not healthy: enabled but queue processor not running")
                return False
            if self.max_requests <= 0 or self.time_window <= 0 or self.max_queue_size <= 0:
                logger.debug("Rate limiter not healthy: invalid configuration values")
                return False
            return True
        except Exception as health_error:
            logger.error(f"Error checking rate limiter health: {health_error}")
            return False

    def get_health_report(self) -> dict:
        try:
            health_issues = []
            if self._initialization_error:
                health_issues.append("Initialization error occurred")
            if self.enabled:
                if not self.queue_processor_task:
                    health_issues.append("Queue processor task not created")
                elif self.queue_processor_task.done():
                    health_issues.append("Queue processor task has stopped")
            if self.max_requests <= 0:
                health_issues.append(f"Invalid max_requests: {self.max_requests}")
            if self.time_window <= 0:
                health_issues.append(f"Invalid time_window: {self.time_window}")
            if self.max_queue_size <= 0:
                health_issues.append(f"Invalid max_queue_size: {self.max_queue_size}")
            total_queued = self.request_queue.qsize() + self.priority_queue.qsize()
            if total_queued >= self.max_queue_size * 0.9:
                health_issues.append(f"Queues nearly full: {total_queued}/{self.max_queue_size}")
            is_healthy = len(health_issues) == 0
            return {
                'healthy': is_healthy,
                'enabled': self.enabled,
                'initialization_error': self._initialization_error,
                'issues': health_issues,
                'queue_processor_running': self.queue_processor_task and not self.queue_processor_task.done() if self.queue_processor_task else False,
                'total_queued': total_queued,
                'max_queue_size': self.max_queue_size,
                'active_users': len(self.user_requests),
                'configuration': {
                    'max_requests': self.max_requests,
                    'time_window': self.time_window,
                    'max_queue_size': self.max_queue_size
                }
            }
        except Exception as report_error:
            logger.error(f"Error generating health report: {report_error}")
            return {
                'healthy': False,
                'error': str(report_error),
                'issues': [f"Error generating health report: {report_error}"]
            }

    def get_error_statistics(self) -> dict:
        try:
            return {
                'initialization_error': self._initialization_error,
                'enabled': self.enabled,
                'queue_processor_status': {
                    'running': (
                        self.queue_processor_task and
                        not self.queue_processor_task.done()
                    ) if self.queue_processor_task else False,
                    'task_exists': self.queue_processor_task is not None,
                    'exception': (
                        str(self.queue_processor_task.exception())
                        if self.queue_processor_task and self.queue_processor_task.done() and self.queue_processor_task.exception()
                        else None
                    )
                },
                'queue_status': {
                    'regular_size': self.request_queue.qsize(),
                    'priority_size': self.priority_queue.qsize(),
                    'total_size': self.request_queue.qsize() + self.priority_queue.qsize(),
                    'max_size': self.max_queue_size,
                    'utilization_percent': round(
                        ((self.request_queue.qsize() + self.priority_queue.qsize()) / self.max_queue_size) * 100, 2
                    ) if self.max_queue_size > 0 else 0
                },
                'active_users': len(self.user_requests),
                'configuration_valid': not self._initialization_error,
                'graceful_degradation_active': not self.enabled or self._initialization_error
            }
        except Exception as stats_error:
            logger.error(f"Error generating error statistics: {stats_error}")
            logger.debug(f"Error statistics generation failed: {type(stats_error).__name__}: {stats_error}")
            return {
                'error': f"Failed to generate statistics: {stats_error}",
                'healthy': False
            }

    async def get_user_queue_position(self, user_id: int) -> dict:
        user_priority = await self.get_user_priority(user_id)
        priority_position = None
        regular_position = None

        if user_priority == 'authorized':
            queue = list(self.priority_queue._queue)
            for idx, req in enumerate(queue):
                if req.get('user_id') == user_id:
                    priority_position = idx + 1
                    break

        if user_priority == 'regular':
            queue = list(self.request_queue._queue)
            for idx, req in enumerate(queue):
                if req.get('user_id') == user_id:
                    regular_position = idx + 1
                    break

        return {
            'user_priority': user_priority,
            'priority_queue_position': priority_position,
            'regular_queue_position': regular_position,
            'priority_queue_size': self.priority_queue.qsize(),
            'regular_queue_size': self.request_queue.qsize(),
            'bypasses_rate_limit': user_priority == 'owner'
        }

async def handle_rate_limited_request(bot: Client, message: Message, handler_type: str, **kwargs) -> bool:
    try:
        if not message or not message.from_user:
            logger.error("Cannot handle rate limited request without user information")
            return False
        user_id = message.from_user.id
        logger.debug(f"Handling rate limited request for user {user_id}, handler: {handler_type}")
        if rate_limiter.is_owner(user_id):
            logger.warning(f"Owner {user_id} reached rate limited handler - this should not happen")
            return False
        try:
            user_priority = await rate_limiter.get_user_priority(user_id)
            is_priority_user = user_priority == 'authorized'
            logger.debug(f"User {user_id} priority: {user_priority}")
        except Exception as priority_error:
            logger.error(f"Error determining user priority for {user_id}: {priority_error}")
            user_priority = 'regular'
            is_priority_user = False
        notification_msg = kwargs.get("notification_msg")
        if not notification_msg:
            try:
                notification_msg = await send_queue_notification(bot, message, priority=is_priority_user)
            except Exception as notification_error:
                logger.error(f"Error sending queue notification to user {user_id}: {notification_error}")
        
        request_data = {
            'user_id': user_id,
            'bot': bot,
            'message': message,
            'handler_type': handler_type,
            'kwargs': {**kwargs, "notified": True, "notification_msg": notification_msg},
            'timestamp': time.time(),
            'priority': is_priority_user,
            'user_priority': user_priority,
        }
        try:
            success = await rate_limiter.add_to_queue(request_data, priority=is_priority_user)
            if success:
                logger.debug(f"Successfully queued {user_priority} user {user_id} request (priority: {is_priority_user})")
                return True
            else:
                try:
                    await send_queue_full_message(bot, message)
                except Exception as error_msg_error:
                    logger.error(f"Error sending queue full message to user {user_id}: {error_msg_error}")
                logger.warning(f"Failed to queue {user_priority} user {user_id} request - queue full")
                return False
        except QueueFullError:
            logger.warning(f"Queue full when trying to add request for user {user_id}")
            try:
                await send_queue_full_message(bot, message)
            except Exception as error_msg_error:
                logger.error(f"Error sending queue full message to user {user_id}: {error_msg_error}")
            return False
        except Exception as queue_error:
            logger.error(f"Error adding request to queue for user {user_id}: {queue_error}")
            try:
                await send_queue_full_message(bot, message)
            except Exception as error_msg_error:
                logger.error(f"Error sending error message to user {user_id}: {error_msg_error}")
            return False
    except Exception as handle_error:
        logger.error(f"Critical error handling rate limited request: {handle_error}")
        return False

async def send_queue_notification(bot: Client, message: Message, priority: bool = False):
    try:
        if not message or not message.from_user:
            logger.error("Cannot send queue notification without valid message")
            return None
        user_id = message.from_user.id
        try:
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
            time_window = rate_limiter.time_window // 60
            max_requests = rate_limiter.max_requests

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
        except Exception as status_error:
            logger.error(f"Error getting queue status for notification: {status_error}")
            s = ""
            s1 = ""
            s2 = ""
            if priority:
                queue_message = MSG_RATE_LIMIT_QUEUE_PRIORITY.format(
                    wait_estimate=1,
                    s=s
                )
            else:
                queue_message = MSG_RATE_LIMIT_QUEUE_REGULAR.format(
                    wait_estimate=1,
                    max_requests=5,
                    time_window=1,
                    s1=s1,
                    s2=s2
                )
        try:
            sent_msg = await handle_flood_wait(
                bot.send_message,
                chat_id=message.chat.id,
                text=queue_message,
                reply_parameters=ReplyParameters(message_id=message.id)
            )
            logger.debug(f"Sent {'priority' if priority else 'regular'} queue notification to user {user_id}")
            return sent_msg
        except FloodWait as flood_error:
            logger.warning(f"FloodWait sending queue notification to user {user_id}: {flood_error}")
        except RPCError as rpc_error:
            logger.error(f"RPC error sending queue notification to user {user_id}: {rpc_error}")
        except Exception as send_error:
            logger.error(f"Unexpected error sending queue notification to user {user_id}: {send_error}")
        return None
    except Exception as notification_error:
        logger.error(f"Critical error in send_queue_notification: {notification_error}")
        return None

async def send_queue_full_message(bot: Client, message: Message) -> None:
    try:
        if not message or not message.from_user:
            logger.error("Cannot send queue full message without valid message")
            return
        user_id = message.from_user.id
        try:
            status = rate_limiter.get_queue_status()
            wait_estimate = max(5, min(30, status['total_queued'] // 5))
            s = "s" if wait_estimate > 1 else ""
            error_message = MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=wait_estimate, s=s)
        except Exception as status_error:
            logger.error(f"Error getting queue status for full message: {status_error}")
            error_message = MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=10, s="")
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=error_message,
                reply_parameters=ReplyParameters(message_id=message.id)
            )
            logger.debug(f"Sent queue full message to user {user_id}")
        except FloodWait as flood_error:
            logger.warning(f"FloodWait sending queue full message to user {user_id}: {flood_error}")
        except RPCError as rpc_error:
            logger.error(f"RPC error sending queue full message to user {user_id}: {rpc_error}")
        except Exception as send_error:
            logger.error(f"Unexpected error sending queue full message to user {user_id}: {send_error}")
    except Exception as full_message_error:
        logger.error(f"Critical error in send_queue_full_message: {full_message_error}")

async def send_queue_status_message(bot: Client, message: Message) -> None:
    try:
        if not message or not message.from_user:
            logger.error("Cannot send queue status without valid message")
            return
        user_id = message.from_user.id
        try:
            status = rate_limiter.get_queue_status()
            enabled_status = "Enabled" if status.get('enabled') else "Disabled"
            status_text = MSG_RATE_LIMIT_QUEUE_STATUS.format(
                regular_queue_size=status['regular_queue_size'],
                priority_queue_size=status['priority_queue_size'],
                total_queued=status['total_queued'],
                max_queue_size=status['max_queue_size'],
                enabled_status=enabled_status,
                active_users=status['active_users'],
                max_requests_per_period=status['max_requests_per_period'],
                time_window_seconds=status['time_window_seconds']
            )
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=status_text,
                    reply_parameters=ReplyParameters(message_id=message.id)
                )
                logger.debug(f"Sent queue status to user {user_id}")
            except FloodWait as flood_error:
                logger.warning(f"FloodWait sending queue status to user {user_id}: {flood_error}")
            except RPCError as rpc_error:
                logger.error(f"RPC error sending queue status to user {user_id}: {rpc_error}")
            except Exception as send_error:
                logger.error(f"Unexpected error sending queue status to user {user_id}: {send_error}")
        except Exception as status_error:
            logger.error(f"Error getting queue status: {status_error}")
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=MSG_RATE_LIMIT_QUEUE_STATUS_ERROR,
                    reply_parameters=ReplyParameters(message_id=message.id)
                )
            except Exception as error_send_error:
                logger.error(f"Error sending status error message: {error_send_error}")
    except Exception as status_message_error:
        logger.error(f"Critical error in send_queue_status_message: {status_message_error}")

rate_limiter = RateLimiter()
