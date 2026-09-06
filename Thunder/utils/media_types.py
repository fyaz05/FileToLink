# Thunder/utils/media_types.py

"""Single source of truth for media-type -> extension / mime / display maps.

Historically three drifted copies existed (``custom_dl.get_file_info_sync``,
``file_properties.get_fname``, ``common.send_file_dc``); this module replaces
all of them (plan H4c).  Both naming families are keyed: pyrogram class
names come out lower-cased with no underscore (``videonote``) while message
attribute names use ``video_note`` -- both are accepted everywhere.
"""

# message attribute name -> stable canonical key
_ATTR_TO_MEDIA_TYPE: dict[str, str] = {
    "audio": "audio",
    "document": "document",
    "photo": "photo",
    "sticker": "sticker",
    "animation": "animation",
    "video": "video",
    "voice": "voice",
    "video_note": "video_note",
}

# pyrogram class-name (lower) -> canonical key
_CLASS_TO_MEDIA_TYPE: dict[str, str] = {
    "audio": "audio",
    "document": "document",
    "photo": "photo",
    "sticker": "sticker",
    "animation": "animation",
    "video": "video",
    "voice": "voice",
    "videonote": "video_note",
    "video_note": "video_note",  # both naming families accepted
}

# canonical key -> (extension, mime type)
_MEDIA_EXT_MIME: dict[str, tuple[str, str]] = {
    "photo": ("jpg", "image/jpeg"),
    "audio": ("mp3", "audio/mpeg"),
    "voice": ("ogg", "audio/ogg"),
    "video": ("mp4", "video/mp4"),
    "animation": ("mp4", "video/mp4"),
    "video_note": ("mp4", "video/mp4"),
    "sticker": ("webp", "image/webp"),
    "document": ("bin", "application/octet-stream"),
}

DEFAULT_EXT = "bin"
DEFAULT_MIME = "application/octet-stream"


def canonical_media_type(*, attr: str | None = None, media: object | None = None) -> str:
    """Resolve a canonical media key from a message attribute or media object."""
    if attr and attr in _ATTR_TO_MEDIA_TYPE:
        return _ATTR_TO_MEDIA_TYPE[attr]
    if media is not None:
        return _CLASS_TO_MEDIA_TYPE.get(type(media).__name__.lower(), "document")
    return "document"


def ext_for(media_key: str) -> str:
    return _MEDIA_EXT_MIME.get(media_key, (DEFAULT_EXT, DEFAULT_MIME))[0]


def mime_for(media_key: str) -> str:
    return _MEDIA_EXT_MIME.get(media_key, (DEFAULT_EXT, DEFAULT_MIME))[1]


def ext_and_mime_for_class(class_name_lower: str) -> tuple[str, str]:
    """Direct lookup by pyrogram class name (``videonote``, ``photo``, ...)."""
    key = _CLASS_TO_MEDIA_TYPE.get(class_name_lower)
    if key is None:
        return DEFAULT_EXT, DEFAULT_MIME
    return _MEDIA_EXT_MIME.get(key, (DEFAULT_EXT, DEFAULT_MIME))


__all__ = [
    "canonical_media_type",
    "ext_for",
    "mime_for",
    "ext_and_mime_for_class",
    "DEFAULT_EXT",
    "DEFAULT_MIME",
]
