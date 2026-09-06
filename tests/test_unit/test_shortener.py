# tests/test_unit/test_shortener.py
"""H5b/M5: plugin registry lookup, offline builders, host validation."""

import pytest

from Thunder.utils.shortener import (
    BitlyPlugin,
    GenericShortenerPlugin,
    LinkvertisePlugin,
    ShortenerSystem,
)


@pytest.mark.unit
def test_registry_lookup_bitly():
    system = ShortenerSystem()
    assert system._get_plugin_class("bitly.com") is BitlyPlugin


@pytest.mark.unit
def test_registry_lookup_generic_fallback():
    system = ShortenerSystem()
    assert system._get_plugin_class("shrinkme.dev") is GenericShortenerPlugin


@pytest.mark.unit
async def test_linkvertise_offline_constructor():
    plugin = LinkvertisePlugin()
    out = await plugin.shorten(None, "https://example.com/file", "12345", "linkvertise.com")
    assert any(
        out.startswith(prefix)
        for prefix in (
            "https://link-to.net/",
            "https://up-to-down.net/",
            "https://direct-link.net/",
            "https://file-link.net/",
        )
    )
    assert "12345" in out


@pytest.mark.unit
@pytest.mark.parametrize(
    "short_url,domain,expected",
    [
        ("https://shrinkme.dev/xAbc", "shrinkme.dev", True),
        ("https://evil.example/xAbc", "shrinkme.dev", False),
        ("https://shrinkme.dev.evil.io/xAbc", "shrinkme.dev", False),
    ],
)
def test_host_validation(short_url, domain, expected):
    assert GenericShortenerPlugin._validate_short_url(short_url, domain) is expected


@pytest.mark.unit
async def test_short_url_passthrough_when_not_ready():
    system = ShortenerSystem()
    assert await system.short_url("https://example.com") == "https://example.com"


@pytest.mark.unit
async def test_cache_hit_is_returned_without_http():
    system = ShortenerSystem()
    system.ready = True
    system._cache["https://long.example/a"] = "https://shrinkme.dev/xyz"
    assert await system.short_url("https://long.example/a") == "https://shrinkme.dev/xyz"
