import os

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash_value_1234567890abcdef")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
os.environ.setdefault("BIN_CHANNEL", "-1001234567890")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("OWNER_ID", "99999")

from Thunder.utils.compat import (
    ChatMemberStatus,
    Filters,
)


class TestFilters:
    def test_private_filter_exists(self):
        assert Filters.private is not None

    def test_command_filter(self):
        f = Filters.command("start")
        assert f is not None

    def test_regex_filter(self):
        f = Filters.regex(r"^test")
        assert f is not None

    def test_user_filter(self):
        f = Filters.user(12345)
        assert f is not None


class TestChatMemberStatus:
    def test_constants(self):
        assert ChatMemberStatus.ADMINISTRATOR == "administrator"
        assert ChatMemberStatus.OWNER == "creator"
        assert ChatMemberStatus.MEMBER == "member"
