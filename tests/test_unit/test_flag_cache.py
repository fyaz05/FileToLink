"""TTL-LRU flag cache semantics."""

import pytest

from Thunder.utils.flag_cache import FlagCache


@pytest.mark.unit
async def test_loader_called_once_within_ttl():
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return "value"

    cache = FlagCache(ttl_seconds=60)
    assert await cache.get_or_load("k", loader) == "value"
    assert await cache.get_or_load("k", loader) == "value"
    assert calls["n"] == 1


@pytest.mark.unit
async def test_invalidate_forces_reload():
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return calls["n"]

    cache = FlagCache(ttl_seconds=60)
    assert await cache.get_or_load("k", loader) == 1
    cache.invalidate("k")
    assert await cache.get_or_load("k", loader) == 2


@pytest.mark.unit
async def test_loader_exception_propagates():
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("db down")

    cache = FlagCache()
    with pytest.raises(RuntimeError):
        await cache.get_or_load("k", boom)
    # nothing cached on failure: a retry must call the loader again
    with pytest.raises(RuntimeError):
        await cache.get_or_load("k", boom)
    assert calls["n"] == 2


@pytest.mark.unit
async def test_lru_bound():
    calls: dict[str, int] = {}

    async def loader(v):
        calls[v] = calls.get(v, 0) + 1
        return v

    cache = FlagCache(ttl_seconds=60, max_items=2)
    await cache.get_or_load("a", lambda: loader("a"))
    await cache.get_or_load("b", lambda: loader("b"))
    await cache.get_or_load("c", lambda: loader("c"))
    # oldest evicted: reloading "a" re-invokes the loader, "c" stays cached
    await cache.get_or_load("a", lambda: loader("a"))
    assert calls["a"] == 2
    await cache.get_or_load("c", lambda: loader("c"))
    assert calls["c"] == 1


@pytest.mark.unit
async def test_sweep_drops_expired():
    async def loader():
        return 1

    cache = FlagCache(ttl_seconds=0)  # everything immediately expired
    await cache.get_or_load("k", loader)
    dropped = cache.sweep()
    assert dropped == 1
    assert cache.sweep() == 0  # public-API check: nothing left to drop


@pytest.mark.unit
async def test_concurrent_loaders_single_flight():
    """Cold/expired keys must share ONE loader task, not stampede the backend."""
    import asyncio

    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        await asyncio.sleep(0.02)  # widen the race window
        return "v"

    cache = FlagCache(ttl_seconds=60)
    results = await asyncio.gather(*(cache.get_or_load("k", loader) for _ in range(10)))
    assert results == ["v"] * 10
    assert calls["n"] == 1
    # load-bearing private read: no public API exposes single-flight bookkeeping;
    # a leaked task here would stampede the backend on the next cold key
    assert cache._inflight == {}  # bookkeeping cleaned up


@pytest.mark.unit
async def test_invalidate_mid_load_defeats_stale_store():
    """Regression: invalidate() while a loader is in flight must not let
    that loader re-cache its pre-mutation value for a full TTL."""
    import asyncio

    release = asyncio.Event()
    loads = {"n": 0}

    async def slow_loader():
        loads["n"] += 1
        await release.wait()
        return "pre-mutation"

    cache = FlagCache(ttl_seconds=60)
    task = asyncio.create_task(cache.get_or_load("k", slow_loader))
    for _ in range(100):  # poll: one yield may not schedule the loader yet
        if "k" in cache._inflight:
            break
        await asyncio.sleep(0)
    assert "k" in cache._inflight  # loader registered before we invalidate
    cache.invalidate("k")
    release.set()
    assert await task == "pre-mutation"  # the in-flight caller still gets a value...
    assert cache._data.get("k") is None  # ...but nothing was cached
