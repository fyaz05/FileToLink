import pytest

from Thunder.utils.media_types import (
    canonical_media_type,
    ext_and_mime_for_class,
    ext_for,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "media_type,ext,mime",
    [
        ("photo", "jpg", "image/jpeg"),
        ("voice", "ogg", "audio/ogg"),
        ("videonote", "mp4", "video/mp4"),
        ("video_note", "mp4", "video/mp4"),
        ("animation", "mp4", "video/mp4"),
        ("audio", "mp3", "audio/mpeg"),
        ("sticker", "webp", "image/webp"),
        ("document", "bin", "application/octet-stream"),
    ],
)
def test_class_lookup(media_type, ext, mime):
    assert ext_and_mime_for_class(media_type) == (ext, mime)


@pytest.mark.unit
def test_unknown_class_falls_back():
    assert ext_and_mime_for_class("unknownthing") == ("bin", "application/octet-stream")


@pytest.mark.unit
def test_attr_resolution():
    assert canonical_media_type(attr="video_note") == "video_note"
    assert canonical_media_type(attr="photo") == "photo"
    assert canonical_media_type(attr=None, media=None) == "document"


@pytest.mark.unit
@pytest.mark.parametrize(
    "media_type,ext",
    [
        ("photo", "jpg"),
        ("nope", "bin"),
    ],
)
def test_ext_for(media_type, ext):
    assert ext_for(media_type) == ext
