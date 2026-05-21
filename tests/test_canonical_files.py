import os

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test_hash_value_1234567890abcdef")
os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
os.environ.setdefault("BIN_CHANNEL", "-1001234567890")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017/test")
os.environ.setdefault("OWNER_ID", "99999")

from Thunder.utils.canonical_files import PUBLIC_HASH_LENGTH, build_public_hash


class TestBuildPublicHash:
    def test_returns_correct_length(self):
        result = build_public_hash("test_unique_id")
        assert len(result) == PUBLIC_HASH_LENGTH

    def test_deterministic(self):
        assert build_public_hash("abc") == build_public_hash("abc")

    def test_different_inputs_different_outputs(self):
        assert build_public_hash("abc") != build_public_hash("def")

    def test_hex_format(self):
        result = build_public_hash("test")
        assert all(c in "0123456789abcdef" for c in result)
