from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytdbot
from pytdbot import types

from .media_helpers import _get_media_content


class _FilterPrivate:
    def __call__(self, _: Any, message: types.Message) -> bool:
        chat = getattr(message, "chat", None)
        if chat and isinstance(chat.type, types.ChatTypePrivate):
            return True
        return False


class _FilterIncoming:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return not getattr(message, "is_outgoing", True)


class _FilterCommand:
    def __init__(self, command: str):
        self.command = command.lower()

    def __call__(self, _: Any, message: types.Message) -> bool:
        text = getattr(message, "text", "") or ""
        if not text.startswith("/"):
            return False
        cmd = text.split()[0].split("@")[0][1:].lower()
        return cmd == self.command


class _FilterRegex:
    def __init__(self, pattern: str):
        import re
        self.pattern = re.compile(pattern)

    def __call__(self, _: Any, update: Any) -> bool:
        data = None
        payload = getattr(update, "payload", None)
        if isinstance(payload, types.CallbackQueryPayloadData):
            data = payload.data
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        if data is None:
            text = getattr(update, "text", None)
            if text is not None:
                data = str(text)
        if data is None:
            return False
        return bool(self.pattern.search(data))


class _FilterMedia:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return _get_media_content(message) is not None


class _FilterDocument:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessageDocument)


class _FilterVideo:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessageVideo)


class _FilterPhoto:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessagePhoto)


class _FilterAudio:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessageAudio)


class _FilterVoice:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessageVoiceNote)


class _FilterAnimation:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessageAnimation)


class _FilterVideoNote:
    def __call__(self, _: Any, message: types.Message) -> bool:
        return isinstance(getattr(message, "content", None), types.MessageVideoNote)


class _FilterChannel:
    def __call__(self, _: Any, message: types.Message) -> bool:
        chat = getattr(message, "chat", None)
        if chat and isinstance(chat.type, types.ChatTypeSupergroup):
            return chat.type.is_channel
        return False


class _FilterUser:
    def __init__(self, user_ids):
        if isinstance(user_ids, int):
            self.user_ids = {user_ids}
        else:
            self.user_ids = set(user_ids)

    def __call__(self, _: Any, message: types.Message) -> bool:
        from_id = getattr(message, "from_id", None)
        return from_id in self.user_ids


class _FilterChat:
    def __init__(self, chat_ids):
        if isinstance(chat_ids, int):
            self.chat_ids = {chat_ids}
        else:
            self.chat_ids = set(chat_ids)

    def __call__(self, _: Any, message: types.Message) -> bool:
        return message.chat_id in self.chat_ids


class _FilterAnd:
    def __init__(self, *filters):
        self.filters = filters

    def __call__(self, client: Any, update: Any) -> bool:
        return all(f(client, update) for f in self.filters)


class _FilterOr:
    def __init__(self, *filters):
        self.filters = filters

    def __call__(self, client: Any, update: Any) -> bool:
        return any(f(client, update) for f in self.filters)


class _FilterNot:
    def __init__(self, filt):
        self.filt = filt

    def __call__(self, client: Any, update: Any) -> bool:
        return not self.filt(client, update)


class Filters:
    private = pytdbot.filters.create(_FilterPrivate())
    incoming = pytdbot.filters.create(_FilterIncoming())
    media = pytdbot.filters.create(_FilterMedia())
    document = pytdbot.filters.create(_FilterDocument())
    video = pytdbot.filters.create(_FilterVideo())
    photo = pytdbot.filters.create(_FilterPhoto())
    audio = pytdbot.filters.create(_FilterAudio())
    voice = pytdbot.filters.create(_FilterVoice())
    animation = pytdbot.filters.create(_FilterAnimation())
    video_note = pytdbot.filters.create(_FilterVideoNote())
    channel = pytdbot.filters.create(_FilterChannel())

    @staticmethod
    def command(cmd: str) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterCommand(cmd))

    @staticmethod
    def regex(pattern: str) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterRegex(pattern))

    @staticmethod
    def user(user_ids) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterUser(user_ids))

    @staticmethod
    def chat(chat_ids) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterChat(chat_ids))

    @staticmethod
    def create(func: Callable) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(func)

    @staticmethod
    def and_(*filters) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterAnd(*filters))

    @staticmethod
    def or_(*filters) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterOr(*filters))

    @staticmethod
    def not_(filt) -> pytdbot.filters.Filter:
        return pytdbot.filters.create(_FilterNot(filt))

    @staticmethod
    def outgoing() -> pytdbot.filters.Filter:
        return pytdbot.filters.create(lambda _, m: getattr(m, "is_outgoing", False))

    @staticmethod
    def group() -> pytdbot.filters.Filter:
        def _check(_, m):
            chat = getattr(m, "chat", None)
            if chat:
                return isinstance(chat.type, (types.ChatTypeBasicGroup, types.ChatTypeSupergroup)) and not getattr(chat.type, "is_channel", False)
            return False
        return pytdbot.filters.create(_check)

    @staticmethod
    def supergroup() -> pytdbot.filters.Filter:
        def _check(_, m):
            chat = getattr(m, "chat", None)
            if chat and isinstance(chat.type, types.ChatTypeSupergroup):
                return not chat.type.is_channel
            return False
        return pytdbot.filters.create(_check)
