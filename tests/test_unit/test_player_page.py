"""Player page contract: the Jinja port of ThunderGo's cinema UI.

Pins the exact surface player.min.js (CDN) reads, so a template edit that
renames an id, drops a config key, or breaks a branch fails here — not in a
browser. Hermetic: render_media_page takes an explicit src (no Var, no bot).
"""

import pytest

from Thunder.utils.render_template import render_media_page

pytestmark = pytest.mark.unit


async def _render(name="v.mp4", mime="video/mp4", size=10 * 1024 * 1024, src=None):
    return await render_media_page(
        name,
        src or f"https://files.example/f/abc123/{name}",
        mime_type=mime,
        size_bytes=size,
    )


async def test_cinema_config_contract():
    """Player.min.js reads src/mimeType/fileName/isVideo/isAudio — all present."""
    out = await _render()
    for key in ("src:", "mimeType:", "fileName:", "isVideo:", "isAudio:"):
        assert key in out
    assert "isVideo: true" in out and "isAudio: false" in out
    out = await _render("a.mp3", "audio/mpeg", 1024)
    assert "isVideo: false" in out and "isAudio: true" in out


async def test_cdn_theme_assets():
    out = await _render()
    assert "ThunderGo/player.min.css" in out
    assert "ThunderGo/player.min.js" in out
    assert "Obsidian" not in out


async def test_unsupported_format_plumbing():
    """The codec-failure box the JS unhides must exist with its live region
    on the reason element itself (not merely anywhere on the page)."""
    out = await _render()
    assert 'id="unsupportedFormatMessage"' in out
    assert '<p class="unsupported-reason" aria-live="polite">' in out


async def test_kind_branches():
    video = await _render()
    assert 'view-type="video"' in video
    assert 'class="cinema"' in video
    audio = await _render("a.mp3", "audio/mpeg", 1024)
    assert 'view-type="audio"' in audio
    assert "cinema--audio" in audio and "audio-face" in audio
    image = await _render("p.png", "image/png", 1024)
    assert "image-viewer" in image and "media-player" not in image
    other = await _render("z.zip", "application/zip", 1024)
    assert "non-media" in other and "cinema--fallback" in other
    assert "media-player" not in other


async def test_download_uses_attachment():
    video = await _render()
    assert video.count("disposition=attachment") == 1  # download button only
    other = await _render("z.zip", "application/zip", 1024)
    assert other.count("disposition=attachment") == 2  # button + card
    assert "disposition=inline" in video  # player + intents stay inline


async def test_drawer_upgrade_keys():
    """All 19 static links carry data-player keys the JS rewrites with
    Play-Store fallbacks; static hrefs remain the no-JS fallback."""
    out = await _render()
    assert out.count("data-player=") == 19
    assert "intent:" in out and "vlc-x-callback" in out


async def test_meta_tags():
    out = await _render()
    assert "10.0 MB" in out  # size_formatted
    assert "skeleton-text" in out
    assert "noindex" in out
    nosize = await _render("n.mp4", "video/mp4", None)
    assert "metaSize" not in nosize


async def test_embed_tags():
    """Rich-embed tags per kind; never on image/download pages."""
    video = await _render()
    assert 'property="og:video"' in video
    assert 'property="og:audio"' not in video
    audio = await _render("a.mp3", "audio/mpeg", 1024)
    assert 'property="og:audio"' in audio
    assert 'property="og:video"' not in audio
    image = await _render("p.png", "image/png", 1024)
    assert "og:video" not in image and "og:audio" not in image
    other = await _render("z.zip", "application/zip", 1024)
    assert "og:video" not in other and "og:audio" not in other


async def test_no_go_template_remnants():
    out = await _render()
    for remnant in ("{{.", "{{if ", "{{end}}", "printf", "ThunderGo/logo"):
        assert remnant not in out
