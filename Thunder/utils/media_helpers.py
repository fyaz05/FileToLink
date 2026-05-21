from __future__ import annotations

from pytdbot import types


def _get_media_content(message: types.Message):
    if not hasattr(message, "content") or message.content is None:
        return None
    content = message.content
    media_types = (
        types.MessagePhoto,
        types.MessageVideo,
        types.MessageDocument,
        types.MessageAudio,
        types.MessageVoiceNote,
        types.MessageAnimation,
        types.MessageVideoNote,
        types.MessageSticker,
    )
    if isinstance(content, media_types):
        return content
    return None


def _get_media_file(message: types.Message) -> types.File | None:
    content = getattr(message, "content", None)
    if content is None:
        return None
    if isinstance(content, types.MessageDocument):
        return content.document.document if content.document else None
    if isinstance(content, types.MessageVideo):
        return content.video.video if content.video else None
    if isinstance(content, types.MessagePhoto):
        sizes = content.photo.sizes if content.photo else None
        return sizes[-1].photo if sizes else None
    if isinstance(content, types.MessageAudio):
        return content.audio.audio if content.audio else None
    if isinstance(content, types.MessageVoiceNote):
        return content.voice_note.voice if content.voice_note else None
    if isinstance(content, types.MessageAnimation):
        return content.animation.animation if content.animation else None
    if isinstance(content, types.MessageVideoNote):
        return content.video_note.video if content.video_note else None
    if isinstance(content, types.MessageSticker):
        return content.sticker.sticker if content.sticker else None
    return None


def _get_file_name(message: types.Message) -> str | None:
    content = message.content
    for attr in ("document", "video", "audio", "animation"):
        media = getattr(content, attr, None)
        if media and hasattr(media, "file_name") and media.file_name:
            return media.file_name
    return None


def _get_mime_type(message: types.Message) -> str | None:
    content = message.content
    for attr in ("document", "video", "audio", "animation", "voice_note"):
        media = getattr(content, attr, None)
        if media and hasattr(media, "mime_type") and media.mime_type:
            return media.mime_type
    return None


def _get_file_size(message: types.Message) -> int:
    media_file = _get_media_file(message)
    return media_file.size if media_file else 0


def _get_file_unique_id(message: types.Message) -> str | None:
    return getattr(message, "remote_unique_file_id", None)


def _get_file_id(message: types.Message) -> str | None:
    return getattr(message, "remote_file_id", None)


def _get_file_int_id(message: types.Message) -> int | None:
    media_file = _get_media_file(message)
    return media_file.id if media_file else None


def _infer_mime_from_content_type(content_type: str) -> str | None:
    """Fallback MIME type lookup when TDLib doesn't provide one."""
    _CONTENT_MIME_MAP = {
        "document": "application/octet-stream",
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "voicenote": "audio/ogg",
        "animation": "video/mp4",
        "videonote": "video/mp4",
        "sticker": "image/webp",
        "photo": "image/jpeg",
    }
    return _CONTENT_MIME_MAP.get(content_type)


def _get_extension_for_content_type(content_type: str) -> str:
    """Fallback file extension when no file_name is available."""
    _EXT_MAP = {
        "document": ".bin",
        "video": ".mp4",
        "audio": ".mp3",
        "voicenote": ".ogg",
        "animation": ".mp4",
        "videonote": ".mp4",
        "sticker": ".webp",
        "photo": ".jpg",
    }
    return _EXT_MAP.get(content_type, ".bin")
