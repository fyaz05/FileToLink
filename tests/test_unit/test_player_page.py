"""Player page contract: the Jinja port of ThunderGo's cinema UI.

Pins the exact surface player.min.js (CDN) reads, so a template edit that
renames an id, drops a config key, or breaks a branch fails here — not in a
browser. Hermetic: render_media_page takes an explicit src (no Var, no bot).
"""

import re

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


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        # generic mime: extension sniffing decides (render_template.py:48-55)
        ("clip.mp4", "application/octet-stream", "video"),
        ("song.mp3", "application/octet-stream", "audio"),
        ("shot.png", "application/octet-stream", "image"),
        ("archive.zip", "application/octet-stream", "other"),
        # no mime at all
        ("clip.mkv", None, "video"),
        ("unknown.weirdext", None, "other"),
        # a specific mime is authoritative, extension must not override it
        ("mislabelled.mp3", "video/mp4", "video"),
    ],
)
async def test_the_mime_fallback_actually_runs(name, mime, expected):
    """Every case above used a real mime type, so _page_kind's extension
    sniffing and its application/octet-stream guard were dead code: neutering
    them left the whole file green."""
    out = await _render(name, mime, 1024)
    markers = {
        "video": 'view-type="video"',
        "audio": "cinema--audio",
        "image": "image-viewer",
        "other": "non-media",
    }
    present = [k for k, v in markers.items() if v in out]
    assert markers[expected] in out, (
        f"{name} with mime {mime!r} rendered as {present or 'nothing'}, expected {expected}"
    )
    for kind, marker in markers.items():
        if kind != expected:
            assert marker not in out, f"{name} rendered as {expected} and {kind}"


async def test_download_uses_attachment():
    video = await _render()
    assert video.count("disposition=attachment") == 1  # download button only
    other = await _render("z.zip", "application/zip", 1024)
    assert other.count("disposition=attachment") == 2  # button + card
    assert "disposition=inline" in video  # player + intents stay inline


async def test_drawer_upgrade_keys():
    """All 19 static links carry data-player keys the JS rewrites with
    Play-Store fallbacks; static hrefs remain the no-JS fallback. The key
    NAMES matter (unknown keys render dead hrefs), not just the count."""
    out = await _render()
    keys = set(re.findall(r'data-player="([^"]+)"', out))
    assert keys == {
        "android-vlc",
        "android-mx",
        "android-mx-pro",
        "android-splayer",
        "android-next",
        "android-nova",
        "android-mpv",
        "android-just",
        "android-nplayer",
        "ios-vlc",
        "ios-infuse",
        "ios-nplayer",
        "ios-outplayer",
        "ios-oplayer",
        "desktop-vlc",
        "desktop-potplayer",
        "desktop-iina",
        "desktop-mpv",
        "desktop-kmplayer",
    }
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


# --- the inline fallback must not become the default ------------------------
#
# The inline <style> ships AFTER the CDN theme <link>, so at equal specificity
# it wins. The bundle declares no `display` for .drawer and only `position` for
# .atmosphere, and .file-meta is display:flex at LOWER specificity than
# `.masthead .file-meta` -- so an unscoped `display:none` in this template
# permanently hides the Open-In drawer, the ambient background and the file
# metadata on every page, not only when the CDN is blocked. Those elements'
# ids were all present, which is why an id-presence test never noticed.


def _inline_css(out: str) -> str:
    return "\n".join(re.findall(r"<style>(.*?)</style>", out, re.S))


def _css_rules(css: str) -> list[str]:
    return [r.strip() for r in css.split("}") if "{" in r]


@pytest.mark.parametrize(
    "selector",
    [".drawer", ".atmosphere", ".file-meta", ".cinema"],
)
async def test_fallback_css_never_hides_an_element_unconditionally(selector):
    """Every display:none in the fallback stylesheet must be behind
    .fallback-ready, so a healthy CDN load renders the full page."""
    for rule in _css_rules(_inline_css(await _render())):
        head, _, body = rule.partition("{")
        if "display:none" not in body.replace(" ", ""):
            continue
        for part in head.split(","):
            assert ".fallback-ready" in part, (
                f"{selector.strip()} is hidden unconditionally by `{part.strip()}` -- "
                "this rule applies on every load, not only when a CDN fails"
            )


async def test_fallback_state_is_only_entered_on_a_cdn_failure():
    out = await _render()
    # the class is added by markFallback(), which is only wired to onerror=
    assert "function markFallback()" in out
    assert 'onerror="markFallback()"' in out
    # and never unconditionally from a script that runs on every load
    inline_scripts = "\n".join(re.findall(r"<script(?![^>]*\bsrc=)(.*?)</script>", out, re.S))
    assert "fallback-ready" not in inline_scripts.replace(
        "document.documentElement.classList.add('fallback-ready')", ""
    ), "an always-run script marks the page as fallback, hiding the full UI"


async def test_every_failable_cdn_asset_carries_the_onerror():
    """A substring check for onerror="markFallback()" *somewhere* passed with 6 of
    7 assets unwired: a blocked jsdelivr then never engages the fallback on a
    video/audio page and the user gets the half-broken layout."""
    out = await _render()
    cdn_assets = re.findall(
        r'<(?:link|script)\b[^>]*?(?:href|src)="https://[^"]*"[^>]*>', out, re.S
    )
    # preconnect is a hint that can fail harmlessly, the font links carry
    # onload=this.media, and the logo is decorative. The player assets are the
    # ones whose failure must degrade the page.
    player_assets = [
        a
        for a in cdn_assets
        if 'rel="preconnect"' not in a and "fonts.googleapis" not in a and "logo.png" not in a
    ]
    assert len(player_assets) >= 5, (
        f"the scan found only {len(player_assets)} CDN assets -- the template "
        "changed shape and this test no longer examines anything"
    )
    unwired = [a[:90] for a in player_assets if "markFallback()" not in a]
    assert not unwired, (
        f"{len(unwired)} of {len(player_assets)} CDN assets can fail without "
        f"triggering the fallback: {unwired}"
    )


async def test_the_css_and_the_js_agree_on_the_fallback_class():
    """Each side was substring-checked independently. Renaming the CSS to
    `.fallback-ready-v2` while the JS keeps `.fallback-ready` leaves the entire
    fallback stylesheet inert -- and the suite green."""
    out = await _render()
    css = _inline_css(out)

    scoped = set(re.findall(r"\.([A-Za-z][\w-]*)", css))
    added = set(re.findall(r"classList\.add\(\s*'([^']+)'", out))
    assert added, "the page never adds a fallback class"

    conditional = {c for c in scoped if "fallback" in c}
    assert conditional, f"the fallback stylesheet conditions on nothing (classes: {sorted(scoped)})"
    assert conditional == added, (
        f"the stylesheet keys on {sorted(conditional)} but the JS adds "
        f"{sorted(added)} -- the fallback can never engage"
    )


async def test_fallback_css_is_scoped_to_the_fallback_state():
    """The whole block must be inert when the CDN is reachable."""
    css = _inline_css(await _render())
    assert css.strip(), "expected an inline fallback stylesheet"
    for rule in _css_rules(css):
        head = rule.partition("{")[0]
        for part in head.split(","):
            assert ".fallback-ready" in part, (
                f"unscoped fallback rule `{part.strip()}` overrides the CDN theme "
                "on every page load"
            )


async def test_fallback_marker_is_defined_before_any_use():
    """onerror="markFallback()" fires as soon as the asset fails, which for the
    head <link> tags is during parsing -- before <body> exists."""
    out = await _render()
    defined = out.index("function markFallback()")
    for match in re.finditer(r'onerror="markFallback\(\)"', out):
        assert match.start() > defined, "an onerror handler fires before markFallback is defined"
    assert "document.documentElement.classList.add('fallback-ready')" in out


async def test_drawer_and_chrome_exist_so_they_can_be_shown():
    """The drawer the fallback used to hide -- its markup must stay."""
    out = await _render()
    assert 'id="playersDrawer"' in out
    assert 'id="playersPanel"' in out or 'id="playersDrawer"' in out
    assert 'class="atmosphere"' in out
    assert 'id="fileMeta"' in out
