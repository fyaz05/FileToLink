"""Escape and layout checks for the player page.

MiniJinja has no contextual autoescaping, so the template depends on |tojson
for the config blob and attribute autoescaping for markup.
"""
import html
import json
import re
from pathlib import Path

import pytest

from Thunder.utils import render_template

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "Thunder" / "template"

BREAKOUT = 'x" onload="alert(1)</script><script>alert(1)</script>'
URL_WITH_AMPERSAND = "https://example.invalid/f/abc123/clip.mp4?disposition=inline&token=xyz"


@pytest.fixture(autouse=True)
def _template_dir(monkeypatch):
    """Resolve templates from the repo, not the process working directory."""
    monkeypatch.setattr(
        render_template.template_env,
        "loader",
        render_template.minijinja.load_from_path([str(TEMPLATE_DIR)]),
    )


def _config_blob(page: str) -> dict:
    """Extract and parse window.__CINEMA_CONFIG__ from a rendered page."""
    match = re.search(r"window\.__CINEMA_CONFIG__ = (\{.*?\});\n", page, re.S)
    assert match, "config blob not found"
    return json.loads(match.group(1))


async def test_video_page_embeds_stream_and_download_urls():
    page = await render_template.render_media_page(
        "clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4", file_size=1024
    )

    assert "clip.mp4" in page
    # The stream URL reaches the player through the JSON config, escaped for JS.
    assert _config_blob(page)["src"] == URL_WITH_AMPERSAND
    # The download link drops the inline disposition query. Unescape first: the
    # engine writes "/" as &#x2f;, which the browser decodes but Python does not.
    download_hrefs = [html.unescape(h) for h in re.findall(r'href="([^"]+)"', page)]
    assert "https://example.invalid/f/abc123/clip.mp4" in download_hrefs
    assert not any("disposition=inline" in h for h in download_hrefs), \
        "the download link must not carry the inline disposition"
    assert "1.0 KB" in page


async def test_unknown_size_renders_a_placeholder():
    page = await render_template.render_media_page("clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4")
    assert "Unknown" in page


async def test_config_blob_is_json_parseable_and_preserves_the_url():
    page = await render_template.render_media_page("clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4")
    config = _config_blob(page)

    assert config["src"] == URL_WITH_AMPERSAND
    assert config["fileName"] == "clip.mp4"
    assert config["mimeType"] == "video/mp4"
    assert config["isVideo"] is True
    assert config["isAudio"] is False


async def test_script_context_does_not_break_out_of_the_script_tag():
    page = await render_template.render_media_page(BREAKOUT, URL_WITH_AMPERSAND, mime_type="video/mp4")

    assert "</script><script>alert(1)" not in page, "filename broke out of the script block"

    config = _config_blob(page)
    assert config["fileName"] == BREAKOUT, "escaping must round-trip, not mangle"


async def test_ampersand_is_escaped_per_context():
    page = await render_template.render_media_page("clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4")

    # In <script> it must be the JS escape, never the HTML entity.
    assert "\\u0026token=xyz" in page
    # In an attribute it must be HTML-escaped; unescape to compare.
    srcs = [html.unescape(v) for v in re.findall(r'src="([^"]+)"', page)]
    assert URL_WITH_AMPERSAND in srcs


async def test_attribute_context_escapes_quotes_and_angles():
    page = await render_template.render_media_page(BREAKOUT, URL_WITH_AMPERSAND, mime_type="video/mp4")

    assert 'onload="alert(1)' not in page, "filename escaped the title attribute"
    title_tags = re.findall(r'<h1 class="title"[^>]*>.*?</h1>', page, re.S)
    assert title_tags, "title element missing"
    assert "<script>" not in title_tags[0]


async def test_audio_page_uses_the_player():
    page = await render_template.render_media_page("song.mp3", URL_WITH_AMPERSAND, mime_type="audio/mpeg")

    assert "<movi-player" in page
    assert "cinema--audio" in page


async def test_image_page_renders_an_image_viewer_and_no_player():
    page = await render_template.render_media_page(
        "photo.jpg", "https://example.invalid/f/abc/photo.jpg", mime_type="image/jpeg"
    )

    assert 'class="image-viewer"' in page
    assert "<movi-player" not in page
    assert "cinema--fallback" in page


async def test_non_media_page_offers_a_download():
    page = await render_template.render_media_page(
        "doc.pdf", "https://example.invalid/f/abc/doc.pdf", mime_type="application/pdf"
    )

    assert 'class="non-media"' in page
    assert "application/pdf" in page
    assert "<movi-player" not in page


async def test_player_is_configured_for_mkv_playback():
    page = await render_template.render_media_page("clip.mkv", URL_WITH_AMPERSAND, mime_type="video/x-matroska")

    # fallback="native" is what keeps an unreadable source from dead-ending.
    assert 'fallback="native"' in page
    assert "controls" in page


@pytest.mark.parametrize(
    "file_name, mime_type, expected",
    [
        # The extension decides, because Telegram labels documents inconsistently.
        ("Fear.Street.1994.480p.WEB-DL.mkv", "application/octet-stream", "video"),
        ("clip.mkv", "application/x-matroska", "video"),
        ("clip.mkv", "", "video"),
        ("clip.MKV", "application/octet-stream", "video"),
        ("clip.ts", "application/octet-stream", "video"),
        ("song.flac", "application/octet-stream", "audio"),
        ("track.mka", "", "audio"),
        ("photo.webp", "application/octet-stream", "image"),
        # No usable extension, so the MIME type decides.
        ("noext", "video/mp4", "video"),
        ("noext", "audio/mpeg", "audio"),
        ("noext", "image/png", "image"),
        ("doc.pdf", "application/pdf", "other"),
        ("archive.zip", "application/zip", "other"),
        ("noext", "application/octet-stream", "other"),
        # A dot that is not an extension must not be read as one.
        ("../etc/passwd", "application/octet-stream", "other"),
        ("file.", "application/octet-stream", "other"),
        ("a.b/c", "application/octet-stream", "other"),
    ],
)
def test_classify_media(file_name, mime_type, expected):
    assert render_template.classify_media(file_name, mime_type) == expected


async def test_octet_stream_mkv_still_gets_the_player():
    """Most real MKV traffic arrives labelled octet-stream."""
    page = await render_template.render_media_page(
        "Fear.Street.1994.480p.WEB-DL.mkv",
        URL_WITH_AMPERSAND,
        mime_type="application/octet-stream",
    )

    assert "<movi-player" in page
    assert 'class="non-media"' not in page
    assert _config_blob(page)["isVideo"] is True


async def test_non_media_copy_does_not_claim_the_browser_cannot_play_it():
    page = await render_template.render_media_page(
        "doc.pdf", "https://example.invalid/f/abc/doc.pdf", mime_type="application/pdf"
    )

    # movi handles containers itself, so only a non-media file says this.
    assert "can&#x27;t be played in the browser" not in page
    assert "nothing to play" in page


async def test_player_cdn_is_pinned():
    page = await render_template.render_media_page("clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4")

    # Playback is the primary path; an unpinned CDN URL would ship releases unreviewed.
    assert "movi-player@0.4.1" in page
    assert "moviplayer.com/dist" not in page


async def test_assets_are_served_locally_not_from_the_cdn():
    page = await render_template.render_media_page("clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4")

    assert "/assets/player.css" in page
    assert "/assets/player.js" in page
    assert "cdn.jsdelivr.net/gh/fyaz05/Resources@main/ThunderGo/player.min" not in page


async def test_asset_urls_are_fingerprinted():
    """The assets are cached immutably, so their URL must change with content."""
    page = await render_template.render_media_page("clip.mp4", URL_WITH_AMPERSAND, mime_type="video/mp4")

    version = render_template.ASSET_VERSION
    assert version and len(version) == 12
    assert f'/assets/player.css?v={version}' in page
    assert f'/assets/player.js?v={version}' in page


async def test_unknown_template_variable_fails_loudly():
    """undefined_behavior="strict" must raise, not render an empty slot."""
    with pytest.raises(Exception):
        render_template.template_env.render_template("req.html", file_name="x")
