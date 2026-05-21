from __future__ import annotations

from typing import Any

from pytdbot import types


def is_error(result: Any) -> bool:
    return isinstance(result, types.Error)


class ChatMemberStatus:
    ADMINISTRATOR = "administrator"
    OWNER = "creator"
    MEMBER = "member"
    LEFT = "left"
    BANNED = "banned"
    RESTRICTED = "restricted"


def get_member_status(chat_member: types.ChatMember) -> str:
    status = chat_member.status
    if isinstance(status, types.ChatMemberStatusCreator):
        return ChatMemberStatus.OWNER
    if isinstance(status, types.ChatMemberStatusAdministrator):
        return ChatMemberStatus.ADMINISTRATOR
    if isinstance(status, types.ChatMemberStatusMember):
        return ChatMemberStatus.MEMBER
    if isinstance(status, types.ChatMemberStatusLeft):
        return ChatMemberStatus.LEFT
    if isinstance(status, types.ChatMemberStatusBanned):
        return ChatMemberStatus.BANNED
    if isinstance(status, types.ChatMemberStatusRestricted):
        return ChatMemberStatus.RESTRICTED
    return "unknown"
