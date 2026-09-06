# Thunder/utils/flag_cache.py

"""Tiny lazy TTL+LRU cache for per-user/per-channel flags.

Mirrors ThunderGo's ``internal/store/cache.go``: values are loaded on first
access, kept for ``ttl_seconds`` and evicted least-recently-used beyond
``max_items``.  A periodic :meth:`sweep` drops expired entries so memory
stays bounded even for bots with large user bases.

Loader exceptions deliberately propagate -- callers implement their own
fail-closed policy (see ``utils/decorators.py``).
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from Thunder.utils.logger import logger

DEFAULT_TTL_SECONDS = 300
DEFAULT_MAX_ITEMS = 4096
_SWEEP_INTERVAL_SECONDS = 300


class FlagCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_items: int = DEFAULT_MAX_ITEMS,
        name: str = "flags",
    ):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.name = name
        self._data: OrderedDict[Hashable, tuple[Any, float]] = OrderedDict()

    def _prune_expired(self, now: float) -> None:
        expired = [key for key, (_, ts) in self._data.items() if now - ts > self.ttl_seconds]
        for key in expired:
            self._data.pop(key, None)

    async def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        now = time.monotonic()
        if key in self._data:
            value, ts = self._data[key]
            if now - ts <= self.ttl_seconds:
                self._data.move_to_end(key)
                return value
            self._data.pop(key, None)

        value = await loader()
        self._data[key] = (value, now)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)
        return value

    def peek(self, key: Hashable) -> tuple[bool, Any]:
        """Non-loading read: ``(hit, value)``."""
        if key not in self._data:
            return False, None
        value, ts = self._data[key]
        if time.monotonic() - ts > self.ttl_seconds:
            self._data.pop(key, None)
            return False, None
        return True, value

    def invalidate(self, *keys: Hashable) -> None:
        for key in keys:
            self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def occupancy(self) -> int:
        return len(self._data)

    async def sweep(self) -> int:
        now = time.monotonic()
        before = len(self._data)
        self._prune_expired(now)
        dropped = before - len(self._data)
        if dropped:
            logger.debug(f"flag_cache[{self.name}]: swept {dropped} expired entries")
        return dropped

    async def run_sweeper(self) -> None:
        """Background loop; cancel to stop.  Registered at startup."""
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"flag_cache[{self.name}] sweeper error: {e}", exc_info=True)


flags = FlagCache(name="user_flags")

__all__ = ["FlagCache", "flags", "DEFAULT_TTL_SECONDS"]
