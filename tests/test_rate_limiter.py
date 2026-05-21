import os

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash_value_1234567890abcdef")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
os.environ.setdefault("BIN_CHANNEL", "-1001234567890")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("OWNER_ID", "99999")

from Thunder.utils.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_is_owner(self):
        from Thunder.vars import Var
        rl = RateLimiter()
        assert rl.is_owner(Var.OWNER_ID) is True
        assert rl.is_owner(999999) is False

    def test_queue_status(self):
        rl = RateLimiter()
        status = rl.get_queue_status()
        assert "regular_queue_size" in status
        assert "priority_queue_size" in status
        assert "total_queued" in status
        assert "enabled" in status
