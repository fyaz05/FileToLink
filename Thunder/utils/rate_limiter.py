# Thunder/utils/rate_limiter.py

import os
import time
import asyncio
from typing import Dict, List, Optional
from pyrogram import Client
from pyrogram.types import Message
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
            try:
                self.max_requests = Var.MAX_FILES_PER_PERIOD
                self.time_window = Var.RATE_LIMIT_PERIOD_MINUTES * 60
                self.max_queue_size = Var.MAX_QUEUE_SIZE
                self.enabled = Var.RATE_LIMIT_ENABLED
                if not self._validate_configuration():
                    logger.warning("Rate limiter disabled due to invalid configuration")
                else:
                    logger.info(f"Rate limiter initialized successfully: max_requests={self.max_requests}, "
                                f"time_window={self.time_window}s, max_queue_size={self.max_queue_size}, "
                                f"enabled={self.enabled}")
            except Exception as config_error:
                logger.error(f"Rate limiter configuration error: {config_error}")
                logger.debug(f"Configuration error details: {type(config_error).__name__}: {config_error}")
                self.max_requests = 5
                self.time_window = 60
                self.max_queue_size = 100
                self.enabled = False
                self._initialization_error = True
                logger.warning("Rate limiter using safe defaults due to configuration error (graceful degradation)")
                logger.info(f"Safe defaults applied: max_requests={self.max_requests}, "
                            f"time_window={self.time_window}s, max_queue_size={self.max_queue_size}, "
                            f"enabled={self.enabled}")
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
            if removed_count > 0:
                logger.debug(f"Memory cleanup: removed {removed_count}/{before_count} inactive users")
            self._last_cleanup_time = current_time
        except Exception as cleanup_error:
            logger.error(f"Error during inactive users cleanup: {cleanup_error}")

    async def check_rate_limit(self, user_id: int) -> bool:
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
            self._cleanup_expired_requests(user_id)
            current_requests = len(self.user_requests.get(user_id, []))
            logger.debug(f"User {user_id} has {current_requests}/{self.max_requests} requests in current window")
            if current_requests < self.max_requests:
                if user_id not in self.user_requests:
                    self.user_requests[user_id] = []
                self.user_requests[user_id].append(time.time())
                logger.debug(f"User {user_id} within rate limit, request recorded")
                return True
            logger.info(f"User {user_id} exceeded rate limit ({current_requests}/{self.max_requests})")
            return False
        except Exception as rate_check_error:
            logger.error(f"Error checking rate limit for user {user_id}: {rate_check_error}")
            logger.warning(f"Rate limit check failed for user {user_id}, allowing request to proceed (graceful degradation)")
            logger.debug(f"Rate check error details for user {user_id}: {type(rate_check_error).__name__}: {rate_check_error}")
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
                logger.info(f"Successfully added request to {queue_type} queue for user {user_id} "
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

    async def process_queue(self) -> None:
        logger.info("Queue processor started")
        consecutive_errors = 0
        max_consecutive_errors = 10
        total_processed = 0
        total_errors = 0
        while True:
            try:
                current_time = time.time()
                if current_time - self._last_cleanup_time > self._cleanup_interval:
                    self._cleanup_inactive_users()
                processed_request = False
                if not self.priority_queue.empty():
                    try:
                        request_data = await self.priority_queue.get()
                        await self._process_queued_request(request_data, is_priority=True)
                        self.priority_queue.task_done()
                        processed_request = True
                        consecutive_errors = 0
                        total_processed += 1
                    except Exception as priority_error:
                        logger.error(f"Error processing priority queue request: {priority_error}")
                        self.priority_queue.task_done()
                        consecutive_errors += 1
                        total_errors += 1
                elif not self.request_queue.empty():
                    try:
                        request_data = await self.request_queue.get()
                        await self._process_queued_request(request_data, is_priority=False)
                        self.request_queue.task_done()
                        processed_request = True
                        consecutive_errors = 0
                        total_processed += 1
                    except Exception as regular_error:
                        logger.error(f"Error processing regular queue request: {regular_error}")
                        self.request_queue.task_done()
                        consecutive_errors += 1
                        total_errors += 1
                if not processed_request:
                    await asyncio.sleep(0.1)
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning(f"Too many consecutive errors ({consecutive_errors}), "
                                   f"increasing wait time to prevent error spam. "
                                   f"Total processed: {total_processed}, Total errors: {total_errors}")
                    await asyncio.sleep(5)
                    consecutive_errors = 0
            except asyncio.CancelledError:
                logger.info(f"Queue processor cancelled, shutting down. "
                            f"Final stats - Processed: {total_processed}, Errors: {total_errors}")
                break
            except Exception as processor_error:
                logger.error(f"Critical error in queue processor: {processor_error}")
                consecutive_errors += 1
                total_errors += 1
                wait_time = min(2 ** min(consecutive_errors, 6), 60)
                logger.warning(f"Queue processor waiting {wait_time}s before retry "
                               f"(consecutive errors: {consecutive_errors}). "
                               f"Total processed: {total_processed}, Total errors: {total_errors}")
                await asyncio.sleep(wait_time)
        logger.info(f"Queue processor stopped. Final stats - Processed: {total_processed}, Errors: {total_errors}")

    async def _process_queued_request(self, request_data: dict, is_priority: bool) -> None:
        try:
            user_id = request_data.get('user_id')
            if not user_id:
                logger.error("Queued request missing user_id, skipping")
                raise ProcessingError("Queued request missing user_id")
            logger.debug(f"Processing {'priority' if is_priority else 'regular'} queued request for user {user_id}")
            if self.is_owner(user_id):
                logger.debug(f"Processing owner request {user_id} from queue immediately")
                await self._execute_queued_request(request_data)
                return
            try:
                can_process = await self.check_rate_limit(user_id)
                if can_process:
                    await self._execute_queued_request(request_data)
                    logger.info(f"Successfully processed {'priority' if is_priority else 'regular'} "
                                f"queued request for user {user_id}")
                else:
                    await self._requeue_request(request_data, user_id)
            except Exception as rate_check_error:
                logger.error(f"Error checking rate limit for queued request (user {user_id}): {rate_check_error}")
                logger.warning(f"Processing queued request for user {user_id} despite rate check error (graceful degradation)")
                try:
                    await self._execute_queued_request(request_data)
                    logger.info(f"Successfully processed request for user {user_id} via graceful degradation")
                except Exception as fallback_error:
                    logger.error(f"Fallback processing also failed for user {user_id}: {fallback_error}")
                    raise ProcessingError(f"Both primary and fallback processing failed: {fallback_error}")
        except ProcessingError:
            raise
        except Exception as process_error:
            logger.error(f"Unexpected error processing queued request: {process_error}")
            raise ProcessingError(f"Failed to process queued request: {process_error}")

    async def _requeue_request(self, request_data: dict, user_id: int) -> None:
        try:
            user_priority = await self.get_user_priority(user_id)
            fallback_priority = user_priority == 'authorized'
            try:
                success = await self.add_to_queue(request_data, priority=fallback_priority)
                if success:
                    logger.debug(f"User {user_id} ({user_priority}) still rate limited, "
                                 f"re-queued request (priority: {fallback_priority})")
                else:
                    logger.warning(f"Failed to re-queue request for user {user_id} - queue may be full")
            except QueueFullError:
                logger.warning(f"Cannot re-queue request for user {user_id} - queue is full")
            except Exception as requeue_error:
                if fallback_priority:
                    logger.warning(f"Priority re-queue failed for user {user_id}, "
                                   f"trying regular queue: {requeue_error}")
                    try:
                        await self.add_to_queue(request_data, priority=False)
                        logger.info(f"Successfully re-queued user {user_id} request to regular queue")
                    except Exception as fallback_error:
                        logger.error(f"Failed to re-queue user {user_id} request to regular queue: {fallback_error}")
                else:
                    raise requeue_error
            await asyncio.sleep(min(1.0, 0.1 * (1 + len(self.user_requests.get(user_id, [])))))
        except Exception as requeue_error:
            logger.error(f"Error re-queuing request for user {user_id}: {requeue_error}")

    async def _execute_queued_request(self, request_data: dict) -> None:
        try:
            handler_type = request_data.get('handler_type')
            bot = request_data.get('bot')
            message = request_data.get('message')
            kwargs = request_data.get('kwargs', {})
            user_id = request_data.get('user_id')
            if not handler_type:
                raise ProcessingError("Queued request missing handler_type")
            if not bot:
                raise ProcessingError("Queued request missing bot instance")
            if not message:
                raise ProcessingError("Queued request missing message")
            logger.debug(f"Executing queued {handler_type} request for user {user_id}")
            if handler_type == 'private':
                await self._process_private_request(bot, message, **kwargs)
                logger.info(f"Successfully executed private request for user {user_id}")
            elif handler_type == 'link':
                await self._process_link_request(bot, message, **kwargs)
                logger.info(f"Successfully executed link request for user {user_id}")
            else:
                logger.error(f"Unknown handler type: {handler_type}")
                raise ProcessingError(f"Unknown handler type: {handler_type}")
        except ProcessingError:
            raise
        except Exception as execution_error:
            logger.error(f"Unexpected error executing queued request: {execution_error}")
            raise ProcessingError(f"Failed to execute queued request: {execution_error}")

    async def _process_private_request(self, bot: Client, message: Message, **kwargs) -> None:
        try:
            from Thunder.utils.bot_utils import log_newusr
            from Thunder.utils.decorators import get_shortener_status
            from Thunder.utils.handler import handle_flood_wait
            from Thunder.utils.messages import MSG_PROCESSING_FILE
            from Thunder.bot.plugins.stream import process_single
            user_id = message.from_user.id if message.from_user else None
            logger.debug(f"Processing private request from queue for user {user_id}")
            try:
                shortener_val = await get_shortener_status(bot, message)
                await log_newusr(bot, message.from_user.id, message.from_user.first_name or "")
                status_msg = await handle_flood_wait(message.reply_text, MSG_PROCESSING_FILE, quote=True)
                await process_single(bot, message, message, status_msg, shortener_val)
            except FloodWait as flood_error:
                logger.warning(f"FloodWait error processing private request for user {user_id}: {flood_error}")
                raise
            except RPCError as rpc_error:
                logger.error(f"RPC error processing private request for user {user_id}: {rpc_error}")
                raise ProcessingError(f"RPC error: {rpc_error}")
        except (FloodWait, ProcessingError):
            raise
        except ImportError as import_error:
            logger.error(f"Import error processing private request: {import_error}")
            raise ProcessingError(f"Failed to import required modules: {import_error}")
        except Exception as private_error:
            logger.error(f"Unexpected error processing private request from queue: {private_error}")
            raise ProcessingError(f"Failed to process private request: {private_error}")

    async def _process_link_request(self, bot: Client, message: Message, **kwargs) -> None:
        try:
            from Thunder.bot.plugins.stream import link_handler
            user_id = message.from_user.id if message.from_user else None
            logger.debug(f"Processing link request from queue for user {user_id}")
            kwargs['skip_rate_limit'] = True
            try:
                await link_handler(bot, message, **kwargs)
            except FloodWait as flood_error:
                logger.warning(f"FloodWait error processing link request for user {user_id}: {flood_error}")
                raise
            except RPCError as rpc_error:
                logger.error(f"RPC error processing link request for user {user_id}: {rpc_error}")
                raise ProcessingError(f"RPC error: {rpc_error}")
        except (FloodWait, ProcessingError):
            raise
        except ImportError as import_error:
            logger.error(f"Import error processing link request: {import_error}")
            raise ProcessingError(f"Failed to import required modules: {import_error}")
        except Exception as link_error:
            logger.error(f"Unexpected error processing link request from queue: {link_error}")
            raise ProcessingError(f"Failed to process link request: {link_error}")

    async def start_queue_processor(self) -> None:
        try:
            if not self.enabled:
                logger.info("Rate limiter disabled, not starting queue processor")
                return
            if self._initialization_error:
                logger.warning("Rate limiter had initialization errors, not starting queue processor")
                return
            if self.queue_processor_task and not self.queue_processor_task.done():
                logger.warning("Queue processor task already running")
                return
            logger.info("Starting queue processor task")
            self.queue_processor_task = asyncio.create_task(self.process_queue())
            logger.info("Queue processor task started successfully")
        except Exception as start_error:
            logger.error(f"Error starting queue processor: {start_error}")
            raise RateLimiterError(f"Failed to start queue processor: {start_error}")

    async def shutdown(self) -> None:
        logger.info("Shutting down rate limiter...")
        shutdown_errors = []
        try:
            if hasattr(self, 'queue_processor_task') and self.queue_processor_task:
                if not self.queue_processor_task.done():
                    logger.debug("Cancelling queue processor task")
                    self.queue_processor_task.cancel()
                    try:
                        await asyncio.wait_for(self.queue_processor_task, timeout=5.0)
                    except asyncio.CancelledError:
                        logger.debug("Queue processor task cancelled successfully")
                    except asyncio.TimeoutError:
                        logger.warning("Queue processor task did not stop within timeout")
                else:
                    logger.debug("Queue processor task already done")
        except Exception as task_error:
            error_msg = f"Error cancelling queue processor task: {task_error}"
            logger.error(error_msg)
            shutdown_errors.append(error_msg)
        try:
            cleared_regular = 0
            while not self.request_queue.empty():
                try:
                    self.request_queue.get_nowait()
                    self.request_queue.task_done()
                    cleared_regular += 1
                except asyncio.QueueEmpty:
                    break
            if cleared_regular > 0:
                logger.info(f"Cleared {cleared_regular} requests from regular queue")
        except Exception as regular_queue_error:
            error_msg = f"Error clearing regular queue: {regular_queue_error}"
            logger.error(error_msg)
            shutdown_errors.append(error_msg)
        try:
            cleared_priority = 0
            while not self.priority_queue.empty():
                try:
                    self.priority_queue.get_nowait()
                    self.priority_queue.task_done()
                    cleared_priority += 1
                except asyncio.QueueEmpty:
                    break
            if cleared_priority > 0:
                logger.info(f"Cleared {cleared_priority} requests from priority queue")
        except Exception as priority_queue_error:
            error_msg = f"Error clearing priority queue: {priority_queue_error}"
            logger.error(error_msg)
            shutdown_errors.append(error_msg)
        try:
            active_users = len(self.user_requests)
            self.user_requests.clear()
            if active_users > 0:
                logger.info(f"Cleared rate limit data for {active_users} users")
        except Exception as tracking_error:
            error_msg = f"Error clearing user request tracking: {tracking_error}"
            logger.error(error_msg)
            shutdown_errors.append(error_msg)
        if shutdown_errors:
            logger.warning(f"Rate limiter shutdown completed with {len(shutdown_errors)} errors")
            for error in shutdown_errors:
                logger.warning(f"Shutdown error: {error}")
        else:
            logger.info("Rate limiter shutdown completed successfully")

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
                logger.info(f"Reset rate limits for user {user_id}")
        else:
            self.user_requests.clear()
            logger.info("Reset rate limits for all users")

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
        if user_priority == 'authorized':
            priority_position = 1
        regular_position = None
        if user_priority == 'regular':
            regular_position = 1
        return {
            'user_priority': user_priority,
            'priority_queue_position': priority_position,
            'regular_queue_position': regular_position,
            'priority_queue_size': self.priority_queue.qsize(),
            'regular_queue_size': self.request_queue.qsize(),
            'bypasses_rate_limit': user_priority == 'owner'
        }

async def handle_rate_limited_request(bot: Client, message: Message, handler_type: str, priority: bool = False, **kwargs) -> bool:
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
        request_data = {
            'user_id': user_id,
            'bot': bot,
            'message': message,
            'handler_type': handler_type,
            'kwargs': kwargs,
            'timestamp': time.time(),
            'priority': is_priority_user,
            'user_priority': user_priority
        }
        try:
            success = await rate_limiter.add_to_queue(request_data, priority=is_priority_user)
            if success:
                try:
                    await send_queue_notification(bot, message, priority=is_priority_user)
                except Exception as notification_error:
                    logger.error(f"Error sending queue notification to user {user_id}: {notification_error}")
                logger.info(f"Successfully queued {user_priority} user {user_id} request (priority: {is_priority_user})")
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

async def send_queue_notification(bot: Client, message: Message, priority: bool = False) -> None:
    try:
        if not message or not message.from_user:
            logger.error("Cannot send queue notification without valid message")
            return
        user_id = message.from_user.id
        try:
            status = rate_limiter.get_queue_status()
            queue_size = status['priority_queue_size'] if priority else status['regular_queue_size']
            wait_estimate = min(5, max(1, queue_size // 10 + 1))
            time_window = rate_limiter.time_window // 60
            max_requests = rate_limiter.max_requests
            
            if priority:
                queue_message = MSG_RATE_LIMIT_QUEUE_PRIORITY.format(wait_estimate=wait_estimate)
            else:
                queue_message = MSG_RATE_LIMIT_QUEUE_REGULAR.format(
                    wait_estimate=wait_estimate,
                    max_requests=max_requests,
                    time_window=time_window
                )
        except Exception as status_error:
            logger.error(f"Error getting queue status for notification: {status_error}")
            # Use message constants even in fallback case with default values
            if priority:
                queue_message = MSG_RATE_LIMIT_QUEUE_PRIORITY.format(wait_estimate=1)
            else:
                queue_message = MSG_RATE_LIMIT_QUEUE_REGULAR.format(
                    wait_estimate=1,
                    max_requests=5,
                    time_window=1
                )
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=queue_message,
                reply_to_message_id=message.id
            )
            logger.debug(f"Sent {'priority' if priority else 'regular'} queue notification to user {user_id}")
        except FloodWait as flood_error:
            logger.warning(f"FloodWait sending queue notification to user {user_id}: {flood_error}")
        except RPCError as rpc_error:
            logger.error(f"RPC error sending queue notification to user {user_id}: {rpc_error}")
        except Exception as send_error:
            logger.error(f"Unexpected error sending queue notification to user {user_id}: {send_error}")
    except Exception as notification_error:
        logger.error(f"Critical error in send_queue_notification: {notification_error}")

async def send_queue_full_message(bot: Client, message: Message) -> None:
    try:
        if not message or not message.from_user:
            logger.error("Cannot send queue full message without valid message")
            return
        user_id = message.from_user.id
        try:
            status = rate_limiter.get_queue_status()
            wait_estimate = max(5, min(30, status['total_queued'] // 5))
            error_message = MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=wait_estimate)
        except Exception as status_error:
            logger.error(f"Error getting queue status for full message: {status_error}")
            # Use MSG_RATE_LIMIT_QUEUE_FULL with a default wait estimate
            error_message = MSG_RATE_LIMIT_QUEUE_FULL.format(wait_estimate=10)
        try:
            await bot.send_message(
                chat_id=message.chat.id,
                text=error_message,
                reply_to_message_id=message.id
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
            status_text = MSG_RATE_LIMIT_QUEUE_STATUS.format(
                regular_queue_size=status['regular_queue_size'],
                priority_queue_size=status['priority_queue_size'],
                total_queued=status['total_queued'],
                max_queue_size=status['max_queue_size'],
                enabled=status['enabled'],
                active_users=status['active_users'],
                max_requests_per_period=status['max_requests_per_period'],
                time_window_seconds=status['time_window_seconds']
            )
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=status_text,
                    reply_to_message_id=message.id
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
                    reply_to_message_id=message.id
                )
            except Exception as error_send_error:
                logger.error(f"Error sending status error message: {error_send_error}")
    except Exception as status_message_error:
        logger.error(f"Critical error in send_queue_status_message: {status_message_error}")

rate_limiter = RateLimiter()
