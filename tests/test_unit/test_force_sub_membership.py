"""Force-sub membership must be decided by status, not by "not None".

get_chat_member only raises UserNotParticipant when the user is absent from the
channel entirely. Banned and left members are returned as a ChatMember, so the
previous `member is not None` admitted exactly the users a channel removed --
the gate failed open for them.
"""

import pytest
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import UserNotParticipant

from Thunder.utils import force_channel as fc


class _Member:
    def __init__(self, status, is_member=True):
        self.status = status
        self.is_member = is_member


def _client_returning(value):
    class _Client:
        async def get_chat_member(self, *args, **kwargs):
            if isinstance(value, Exception):
                raise value
            return value

    return _Client()


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER],
)
async def test_active_members_pass(status):
    assert await fc._is_member(_client_returning(_Member(status)), 7) is True


@pytest.mark.unit
@pytest.mark.parametrize("status", [ChatMemberStatus.BANNED, ChatMemberStatus.LEFT])
async def test_removed_members_are_denied(status):
    """Banned/left users come back as a ChatMember, not as an exception."""
    assert await fc._is_member(_client_returning(_Member(status)), 7) is False


@pytest.mark.unit
async def test_restricted_member_passes_only_when_still_in_channel():
    """Muted or slow-mode members are still in the channel; a RESTRICTED
    member flagged is_member=False has been removed."""
    assert (
        await fc._is_member(_client_returning(_Member(ChatMemberStatus.RESTRICTED, True)), 7)
        is True
    )
    assert (
        await fc._is_member(_client_returning(_Member(ChatMemberStatus.RESTRICTED, False)), 7)
        is False
    )


@pytest.mark.unit
async def test_absent_user_is_denied():
    assert await fc._is_member(_client_returning(UserNotParticipant(400, "x", None)), 7) is False
