"""render_media_page drives req.html: the player page must carry the file name
and the source URL with ``disposition=inline``.
"""
from pathlib import Path

from jinja2 import FileSystemLoader

from Thunder.utils import render_template

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "Thunder" / "template"


async def test_render_media_page_returns_player_with_inline_source(monkeypatch):
    """The loader is CWD-relative; point it at the repo's template directory so
    the test does not depend on where pytest was started from."""
    monkeypatch.setattr(render_template.template_env, "loader", FileSystemLoader(str(TEMPLATE_DIR)))

    src = "https://example.invalid/f/abc123/clip.mp4"
    page = await render_template.render_media_page("clip.mp4", src)

    assert "clip.mp4" in page
    assert f"{src}?disposition=inline" in page
