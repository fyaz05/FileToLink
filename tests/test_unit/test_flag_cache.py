# tests/test_unit/test_flag_cache.py
"""H7: TTL-LRU flag cache semantics."""

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
    async def boom():
        raise RuntimeError("db down")

    cache = FlagCache()
    with pytest.raises(RuntimeError):
        await cache.get_or_load("k", boom)
    # nothing cached on failure
    hit, _ = cache.peek("k")
    assert not hit


@pytest.mark.unit
async def test_lru_bound():
    cache = FlagCache(ttl_seconds=60, max_items=2)

    async def loader(v):
        return v

    await cache.get_or_load("a", lambda: loader("a"))
    await cache.get_or_load("b", lambda: loader("b"))
    await cache.get_or_load("c", lambda: loader("c"))
    assert cache.occupancy() == 2
    hit, _ = cache.peek("a")  # oldest evicted
    assert not hit


@pytest.mark.unit
async def test_sweep_drops_expired():
    async def loader():
        return 1

    cache = FlagCache(ttl_seconds=0)  # everything immediately expired
    await cache.get_or_load("k", loader)
    dropped = await cache.sweep()
    assert dropped == 1
    assert cache.occupancy() == 0
