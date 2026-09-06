# Thunder/utils/rate_limiter.py

"""Queue + rate limiting (plan H6).

Protected UX (report §4.2 keep-list): users still get the queue /
wait-estimate messages; only the internals changed.

H6a: bounded structures + periodic sweep (no unbounded deques/dicts).
H6b: a small worker pool replaces the single executor, so one user's long
FloodWait no longer stalls every other queued request; the sliding window
is charged at *execution* time (the old code charged at enqueue AND again
inside the executor, double-charging every queued request); FloodWait
inside a worker requeues the request with an attempt counter instead of
sleeping the worker.
H6c: a global RPS token-bucket breaker (§5.1b) sheds bursts before they all
hit Telegram-side FLOOD_WAIT at once.
"""

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

from Thunder.utils.logger import logger
from Thunder.utils.messages import (
    MSG_RATE_LIMIT_DROPPED,
    MSG_RATE_LIMIT_QUEUE_FULL,
    MSG_RATE_LIMIT_QUEUE_PRIORITY,
    MSG_RATE_LIMIT_QUEUE_REGULAR,
)
from Thunder.utils.safe_call import edit_safe, send_safe
from Thunder.utils.tokens import allowed
from Thunder.vars import Var

# A queued request is requeued at most this many times (FloodWait storms,
# breaker shedding) before it is dropped and the user notified.
MAX_REQUEST_ATTEMPTS = 5
# Bounded bookkeeping
MAX_TRACKED_USERS = 4096
MAX_TRACKED_FILES = 1024


class QueueFullError(Exception):
    pass


class TokenBucket:
    """Non-blocking RPS token bucket (global circuit breaker, H6c)."""

    def __init__(self, rate_per_second: float, burst_multiplier: float = 2.0):
        self.rate = max(rate_per_second, 0.0)
        self.burst = max(self.rate * burst_multiplier, 1.0)
        self._tokens = self.burst
        self._updated = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def allow(self) -> bool:
        if self.rate <= 0:
            return True
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def retry_after(self) -> float:
        if self.rate <= 0:
            return 0.0
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        return (1.0 - self._tokens) / self.rate

    def available(self) -> float:
        """Current token count (for /stats occupancy), without consuming."""
        self._refill()
        return max(self._tokens, 0.0)


class RateLimiter:
    def __init__(self):
        self.request_queue: deque[dict] = deque()
        self.priority_queue: deque[dict] = deque()
        self.user_queue_counts: dict[int, int] = {}

        self.request_event: asyncio.Event = asyncio.Event()
        self.request_lock: asyncio.Lock = asyncio.Lock()

        self.user_requests: dict[int, deque[float]] = {}
        self.global_requests: deque[float] = deque()
        self._deferred_timer: asyncio.TimerHandle | None = None

        self.processing_times: deque[float] = deque(maxlen=100)
        self.file_processing_times: dict[str, deque[float]] = {}
        self.average_processing_time: float = 1.0

        self._initialization_error = False
        self._load_configuration()
        self.breaker = TokenBucket(self._breaker_rate())

    def _breaker_rate(self) -> float:
        if Var.GLOBAL_RPS_LIMIT and Var.GLOBAL_RPS_LIMIT > 0:
            return Var.GLOBAL_RPS_LIMIT
        if self.global_rate_limit_enabled and self.max_global_requests_per_minute > 0:
            return self.max_global_requests_per_minute / 60.0
        return 0.0

    def _load_configuration(self):
        try:
            self.max_requests_per_period = Var.MAX_FILES_PER_PERIOD
            self.rate_limit_period_seconds = Var.RATE_LIMIT_PERIOD_MINUTES * 60
            self.max_queue_size = Var.MAX_QUEUE_SIZE
            self.enabled = Var.RATE_LIMIT_ENABLED
            self.global_rate_limit_enabled = Var.GLOBAL_RATE_LIMIT
            self.max_global_requests_per_minute = Var.MAX_GLOBAL_REQUESTS_PER_MINUTE
            if Var.GLOBAL_RPS_LIMIT and not self.global_rate_limit_enabled:
                # M6 philosophy: surface dead knobs instead of silently
                # ignoring them -- the RPS cap only bites when the breaker
                # itself is enabled (see _breaker_rate)
                logger.warning(
                    "GLOBAL_RPS_LIMIT is set but GLOBAL_RATE_LIMIT is disabled; "
                    "the per-second cap has no effect until the global breaker is enabled."
                )

            if not self._validate_configuration():
                logger.warning("Rate limiter disabled due to invalid configuration.")
                self.enabled = False
            else:
                logger.debug(
                    f"Rate limiter initialized: enabled={self.enabled}, "
                    f"max_requests={self.max_requests_per_period}, "
                    f"period={self.rate_limit_period_seconds}s, "
                    f"queue_size={self.max_queue_size}, "
                    f"global_enabled={self.global_rate_limit_enabled}, "
                    f"max_global_requests={self.max_global_requests_per_minute}"
                )
        except Exception as e:
            logger.critical(
                f"Critical error initializing rate limiter, using safe defaults: {e}", exc_info=True
            )
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
            logger.error(
                "Invalid MAX_GLOBAL_REQUESTS_PER_MINUTE: must be > 0 when global rate limit is enabled."
            )
            is_valid = False
        return is_valid

    def is_owner(self, user_id: int) -> bool:
        return user_id == Var.OWNER_ID

    async def is_authorized_user(self, user_id: int) -> bool:
        try:
            return await allowed(user_id)
        except Exception as e:
            logger.error(f"Database error checking authorized user {user_id}: {e}")
            return False

    async def get_user_priority(self, user_id: int) -> str:
        if self.is_owner(user_id):
            return "owner"
        if await self.is_authorized_user(user_id):
            return "authorized"
        return "regular"

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
        while (
            user_timestamps and user_timestamps[0] <= current_time - self.rate_limit_period_seconds
        ):
            user_timestamps.popleft()
        if len(user_timestamps) >= self.max_requests_per_period:
            return False

        if record:
            if self.global_rate_limit_enabled:
                self.global_requests.append(current_time)
            user_timestamps.append(current_time)
        return True

    # ---------------- sweep (H6a) ----------------

    async def sweep(self) -> dict[str, int]:
        """Prune stale bookkeeping; called every 5 min from the sweeper task."""
        now = time.time()
        dropped_users = 0
        for user_id in list(self.user_requests.keys()):
            stamps = self.user_requests[user_id]
            while stamps and stamps[0] <= now - self.rate_limit_period_seconds:
                stamps.popleft()
            if not stamps:
                self.user_requests.pop(user_id, None)
                dropped_users += 1
        if len(self.user_requests) > MAX_TRACKED_USERS:
            # evict least-recently-active first -- dict-order eviction could
            # wipe an active user's window and grant an instant quota burst
            by_recency = sorted(
                self.user_requests.items(),
                key=lambda kv: kv[1][-1] if kv[1] else 0.0,
            )
            for user_id, _ in by_recency:
                if len(self.user_requests) <= MAX_TRACKED_USERS:
                    break
                self.user_requests.pop(user_id, None)

        dropped_global = 0
        while self.global_requests and self.global_requests[0] <= now - 60:
            self.global_requests.popleft()
            dropped_global += 1

        dropped_files = 0
        if len(self.file_processing_times) > MAX_TRACKED_FILES:
            for key in list(self.file_processing_times.keys()):
                if len(self.file_processing_times) <= MAX_TRACKED_FILES:
                    break
                self.file_processing_times.pop(key, None)
                dropped_files += 1

        dropped_counts = 0
        for user_id, count in list(self.user_queue_counts.items()):
            if count <= 0:
                self.user_queue_counts.pop(user_id, None)
                dropped_counts += 1

        return {
            "user_windows": dropped_users,
            "global_entries": dropped_global,
            "file_entries": dropped_files,
            "stale_counts": dropped_counts,
        }

    def occupancy(self) -> dict[str, float | int]:
        """Limiter occupancy for /stats (plan PR-13)."""
        return {
            "queued": len(self.request_queue) + len(self.priority_queue),
            "tracked_users": len(self.user_requests),
            "global_window": len(self.global_requests),
            "breaker_tokens": round(self.breaker.available(), 2),
        }

    # ---------------- queueing ----------------

    async def _requeue_request(self, request_data: dict, queue_type: str, delay: float = 0.0):
        if delay > 0:
            request_data["not_before"] = time.time() + delay
        async with self.request_lock:
            if queue_type == "priority":
                self.priority_queue.appendleft(request_data)
            else:
                self.request_queue.appendleft(request_data)
            self.request_event.set()
            # A pure requeue (nothing else runnable) must re-park the pool,
            # or the workers spin on the deferred item until not_before.
            if delay > 0:
                self._park_if_all_deferred()
        logger.debug(
            f"Re-queued request for user {request_data['user_id']} to {queue_type} queue (delay={delay:.2f}s)."
        )

    async def add_to_queue(
        self, func: Callable, user_id: int, file_identifier: str | None = None, *args, **kwargs
    ):
        """Queue a request.  The sliding window is NOT charged here -- it is
        charged at execution time (H6b charge-at-exec)."""
        if not self.enabled:
            await func(*args, **kwargs)
            return

        request_data = {
            "func": func,
            "user_id": user_id,
            "args": args,
            "kwargs": kwargs,
            "timestamp": time.time(),
            "user_priority": await self.get_user_priority(user_id),
            "file_identifier": file_identifier,
            "attempts": 0,
            "not_before": 0.0,
        }

        async with self.request_lock:
            total_queued = len(self.request_queue) + len(self.priority_queue)
            if total_queued >= self.max_queue_size:
                raise QueueFullError("Queue is full")

            if request_data["user_priority"] == "authorized":
                self.priority_queue.append(request_data)
                queue_name = "priority"
            else:
                self.request_queue.append(request_data)
                queue_name = "regular"

            self.user_queue_counts[user_id] = self.user_queue_counts.get(user_id, 0) + 1
            logger.debug(
                f"Added request for user {user_id} to {queue_name} queue. Total queued: {total_queued + 1}"
            )
            self.request_event.set()

    # ---------------- executor (H6b) ----------------

    async def _process_one(self) -> bool:
        """Pop and process a single request.  Returns True when something was
        handled (so the worker loop does not spin on an empty event)."""
        async with self.request_lock:
            if self.priority_queue:
                queue, queue_type = self.priority_queue, "priority"
            elif self.request_queue:
                queue, queue_type = self.request_queue, "regular"
            else:
                self.request_event.clear()
                return False
            request_data = queue.popleft()

        now = time.time()
        if request_data.get("not_before", 0.0) > now:
            # deferred: rotate to the right so other requests can proceed
            async with self.request_lock:
                queue.append(request_data)
                self._park_if_all_deferred()
            return True

        user_id = request_data["user_id"]

        # charge-at-exec: the sliding window is charged exactly once, here.
        # Retries (FloodWait/breaker requeues) must not re-charge, or one
        # upload can burn a user's entire window on server-side failures.
        if not self.is_owner(user_id):
            record = not request_data.get("charged")
            if not await self.check_limits(user_id, record=record):
                wait = self._calculate_user_rate_limit_wait(user_id, now)
                if self.global_rate_limit_enabled:
                    wait = max(wait, self._calculate_global_rate_limit_wait(now))
                wait = min(max(wait, 1.0), self.rate_limit_period_seconds)
                await self._requeue_request(request_data, queue_type, delay=wait)
                return True
            if record:
                request_data["charged"] = True
            if self.global_rate_limit_enabled and not self.breaker.allow():
                retry = max(self.breaker.retry_after(), 0.5)
                await self._requeue_request(request_data, queue_type, delay=retry)
                return True

        logger.debug(f"Processing request for user {user_id} from {queue_type} queue.")
        start_time = time.time()
        processed = False
        try:
            await request_data["func"](*request_data["args"], **request_data["kwargs"])
            processing_time = time.time() - start_time
            self.processing_times.append(processing_time)
            if self.processing_times:
                self.average_processing_time = sum(self.processing_times) / len(
                    self.processing_times
                )

            file_identifier = request_data.get("file_identifier")
            if file_identifier:
                file_times = self.file_processing_times.setdefault(
                    file_identifier, deque(maxlen=100)
                )
                file_times.append(processing_time)
            processed = True

        except FloodWait as e:
            # H6b: requeue with an attempt counter instead of stalling the
            # whole worker pool with a sleep.
            attempts = request_data.get("attempts", 0) + 1
            request_data["attempts"] = attempts
            if attempts > MAX_REQUEST_ATTEMPTS:
                logger.warning(
                    f"Dropping request for user {user_id} after {attempts} "
                    f"FloodWait requeues (last wait {e.value}s)."
                )
                processed = True  # leave the queue
                await self._notify_drop(request_data)
            else:
                logger.warning(f"FloodWait for user {user_id}, requeueing (attempt {attempts}).")
                await self._requeue_request(request_data, queue_type, delay=min(e.value, 300.0))
        except asyncio.CancelledError:
            # Shutdown/cancellation: release the queue slot so the user's
            # count does not leak, then propagate.
            async with self.request_lock:
                if user_id in self.user_queue_counts:
                    self.user_queue_counts[user_id] -= 1
                    if self.user_queue_counts[user_id] <= 0:
                        self.user_queue_counts.pop(user_id, None)
            raise
        except Exception as e:
            logger.error(f"Error processing queued request for user {user_id}: {e}", exc_info=True)
            processed = True
        finally:
            if processed:
                async with self.request_lock:
                    if user_id in self.user_queue_counts:
                        self.user_queue_counts[user_id] -= 1
                        if self.user_queue_counts[user_id] <= 0:
                            self.user_queue_counts.pop(user_id, None)
        return True

    async def _notify_drop(self, request_data: dict) -> None:
        notification_msg = request_data["kwargs"].get("notification_msg")
        if notification_msg is None:
            return
        try:
            await edit_safe(notification_msg, MSG_RATE_LIMIT_DROPPED)
        except Exception:
            logger.debug("Could not notify user about dropped request", exc_info=True)

    def _park_if_all_deferred(self) -> None:
        """Stop the busy-spin when every queued request is deferred.

        On Python 3.13, ``Event.wait()`` on a set event and an uncontended
        ``Lock.acquire()`` return WITHOUT yielding.  Workers rotating only
        deferred items therefore had zero yield points and froze the whole
        event loop (all handlers, streams, sweepers) until the earliest
        ``not_before`` passed -- up to 300s at 100% CPU.  Parking clears the
        wakeup event and arms a timer for the earliest deferred request;
        any new enqueue re-sets the event and wakes the pool immediately.
        Caller must hold ``request_lock``.
        """
        now = time.time()
        earliest: float | None = None
        for q in (self.priority_queue, self.request_queue):
            for item in q:
                nb = item.get("not_before", 0.0)
                if nb <= now:
                    # something is runnable -- (re-)wake the pool and keep going
                    self.request_event.set()
                    return
                if earliest is None or nb < earliest:
                    earliest = nb
        if earliest is None:
            return
        self.request_event.clear()
        if self._deferred_timer is not None:
            self._deferred_timer.cancel()
        self._deferred_timer = asyncio.get_running_loop().call_later(
            min(earliest - now, 300.0), self.request_event.set
        )

    async def request_executor(self):
        """One consumer; start :data:`Var.EXECUTOR_WORKERS` -- see start_executors()."""
        logger.debug("Request executor worker started.")
        while True:
            try:
                await self.request_event.wait()
                handled = await self._process_one()
                if not handled:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                logger.debug("Request executor worker cancelled, shutting down.")
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
        if self._deferred_timer is not None:
            self._deferred_timer.cancel()
            self._deferred_timer = None
        logger.debug("Rate limiter queues cleared.")

    # ---------------- estimates (protected UX) ----------------

    async def get_user_queue_position(self, user_id: int) -> dict:
        user_priority = await self.get_user_priority(user_id)
        position = -1
        queue_to_search = (
            self.priority_queue if user_priority == "authorized" else self.request_queue
        )

        for idx, req in enumerate(queue_to_search):
            if req.get("user_id") == user_id:
                position = idx + 1
                break

        effective_position = position
        if user_priority == "regular" and position > -1:
            effective_position += len(self.priority_queue)

        return {
            "user_priority": user_priority,
            "position_in_own_queue": position if position > -1 else None,
            "effective_position": effective_position if effective_position > -1 else None,
            "priority_queue_size": len(self.priority_queue),
            "regular_queue_size": len(self.request_queue),
            "bypasses_rate_limit": user_priority == "owner",
        }

    def _get_base_processing_time(self, file_identifier: str | None) -> float:
        if file_identifier and file_identifier in self.file_processing_times:
            file_times = self.file_processing_times[file_identifier]
            if file_times:
                return sum(file_times) / len(file_times)
        return self.average_processing_time

    async def _calculate_queue_wait(self, user_id: int, effective_processing_time: float) -> float:
        pos_info = await self.get_user_queue_position(user_id)
        items_ahead = (pos_info["effective_position"] - 1) if pos_info["effective_position"] else 0
        return items_ahead * effective_processing_time

    def _calculate_user_rate_limit_wait(self, user_id: int, future_time: float) -> float:
        user_timestamps = self.user_requests.get(user_id, deque())
        future_user_timestamps = deque(
            ts for ts in user_timestamps if ts > future_time - self.rate_limit_period_seconds
        )

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

    async def estimate_wait_time(self, user_id: int, file_identifier: str | None = None) -> float:
        if self.is_owner(user_id):
            return 0.0

        base_processing_time = self._get_base_processing_time(file_identifier)
        min_time_per_request = (
            self.rate_limit_period_seconds / self.max_requests_per_period
            if self.max_requests_per_period > 0
            else 0
        )
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


def start_executors() -> list[asyncio.Task]:
    """Start the worker pool (H6b) -- callers keep the tasks for shutdown."""
    workers: list[asyncio.Task] = []
    for i in range(max(1, int(getattr(Var, "EXECUTOR_WORKERS", 5)))):
        workers.append(
            asyncio.create_task(
                rate_limiter.request_executor(), name=f"request_executor_worker_{i}"
            )
        )
    return workers


async def handle_rate_limited_request(
    bot: Client, message: Message, handler: Callable, *args, **kwargs
):
    rl_user_id = kwargs.pop("rl_user_id", None)
    user_id = (
        rl_user_id
        if rl_user_id is not None
        else (message.from_user.id if message and message.from_user else None)
    )
    if not isinstance(user_id, int):
        logger.error(f"Invalid user_id provided for rate limiting: {user_id}")
        return

    file_identifier = message.document.file_unique_id if message and message.document else None

    if rate_limiter.is_owner(user_id):
        logger.debug(f"Owner {user_id} bypassing rate limit.")
        await handler(bot, message, *args, **kwargs)
        return

    # H6c: probe without consuming -- the queued exec path below is where
    # breaker tokens are consumed.  The immediate path is gated by the 60s
    # user window only (charging here == charging at exec); sub-second
    # breaker throttling therefore applies to queued traffic, not bursts of
    # within-window users.
    if rate_limiter.global_rate_limit_enabled and rate_limiter.breaker.retry_after() > 0:
        logger.warning(f"Global RPS breaker engaged; shedding request for user {user_id}.")
        if not (rl_user_id is not None and rl_user_id < 0):
            await send_queue_full_message(bot, message, file_identifier)
        return

    # Immediate path: executes right now, so charging here == charging at exec.
    if await rate_limiter.check_limits(user_id, record=True):
        logger.debug(f"User {user_id} within rate limits, executing immediately.")
        await handler(bot, message, *args, **kwargs)
        return

    is_channel = rl_user_id is not None and rl_user_id < 0

    if not is_channel:
        try:
            user_priority = await rate_limiter.get_user_priority(user_id)
            notification_msg = await send_queue_notification(
                bot,
                message,
                is_priority=(user_priority == "authorized"),
                file_identifier=file_identifier,
            )
            kwargs["notification_msg"] = notification_msg
        except Exception as e:
            logger.error(f"Error sending queue notification for user {user_id}: {e}", exc_info=True)

    try:
        await rate_limiter.add_to_queue(
            handler, user_id, file_identifier, bot, message, *args, **kwargs
        )
        logger.debug(f"Request for user {user_id} queued.")
    except QueueFullError:
        logger.warning(f"Queue full, request for user {user_id} rejected.")
        if not is_channel:
            await send_queue_full_message(bot, message, file_identifier)
    except Exception as e:
        logger.error(f"Error adding request to queue for user {user_id}: {e}", exc_info=True)
        if not is_channel:
            await send_queue_full_message(bot, message, file_identifier)


async def _send_notification(
    bot: Client, message: Message, template: str, file_identifier: str | None, **format_kwargs
):
    try:
        if message.from_user:
            user_id = message.from_user.id
            wait_seconds = await rate_limiter.estimate_wait_time(user_id, file_identifier)
            wait_estimate = max(1, math.ceil(wait_seconds / 60))

            text = template.format(
                wait_estimate=wait_estimate, s="s" if wait_estimate > 1 else "", **format_kwargs
            )

            return await send_safe(bot, message.chat.id, text=text, reply_to_message_id=message.id)
        else:
            logger.debug("Skipping notification for channel message (no from_user)")
            return None
    except (FloodWait, RPCError) as e:
        who: int | str = message.from_user.id if message.from_user else "channel"
        logger.warning(f"Error sending notification to user {who}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending notification: {e}", exc_info=True)
    return None


async def send_queue_notification(
    bot: Client, message: Message, is_priority: bool, file_identifier: str | None
):
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
            "s2": "s" if time_window > 1 else "",
        }
    user_id = message.from_user.id if message.from_user else "channel"
    logger.debug(
        f"Sending {'priority' if is_priority else 'regular'} queue notification to user {user_id}"
    )
    return await _send_notification(bot, message, template, file_identifier, **params)


async def send_queue_full_message(bot: Client, message: Message, file_identifier: str | None):
    user_id = message.from_user.id if message.from_user else "channel"
    logger.debug(f"Sending queue full message to user {user_id}")
    await _send_notification(bot, message, MSG_RATE_LIMIT_QUEUE_FULL, file_identifier)
