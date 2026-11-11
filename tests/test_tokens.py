"""Token gate: what grants access, and what does not.

The token system is disabled by default in production, so these tests drive
it with an enabled instance and a stubbed database.
"""
import pytest

from Thunder.utils import tokens
from Thunder.vars import Var

USER = 555
OTHER = 556


@pytest.fixture
def token_enabled(monkeypatch):
    monkeypatch.setattr(Var, "TOKEN_ENABLED", True, raising=False)


@pytest.fixture
def token_disabled(monkeypatch):
    monkeypatch.setattr(Var, "TOKEN_ENABLED", False, raising=False)


@pytest.fixture
def no_authorization(monkeypatch):
    async def find_one(query, *args, **kwargs):
        return None

    monkeypatch.setattr(tokens.db.authorized_users_col, "find_one", find_one)


@pytest.fixture
def no_token(monkeypatch):
    async def find_one(query, *args, **kwargs):
        if "expires_at" in query:
            return None
        return None

    monkeypatch.setattr(tokens.db.token_col, "find_one", find_one)


class TestCheck:
    async def test_disabled_system_allows_everyone(self, token_disabled):
        assert await tokens.check(USER) is True

    async def test_owner_always_allowed(self, token_enabled, no_authorization):
        assert await tokens.check(Var.OWNER_ID) is True

    async def test_authorized_user_allowed(self, token_enabled, monkeypatch):
        async def find_one(query, *args, **kwargs):
            return {"_id": "x"}

        monkeypatch.setattr(tokens.db.authorized_users_col, "find_one", find_one)
        assert await tokens.check(USER) is True

    async def test_active_token_allowed(self, token_enabled, no_authorization, monkeypatch):
        async def find_one(query, *args, **kwargs):
            return {"_id": "x"}

        monkeypatch.setattr(tokens.db.token_col, "find_one", find_one)
        assert await tokens.check(USER) is True

    async def test_no_token_and_no_authorization_denied(
            self, token_enabled, no_authorization, no_token):
        assert await tokens.check(USER) is False


class TestAuthorization:
    async def test_authorize_upserts(self, token_enabled, monkeypatch):
        calls = []

        async def update_one(query, update, upsert=False):
            calls.append((query, update, upsert))

            class Result:
                modified_count = 1
            return Result()

        monkeypatch.setattr(tokens.db.authorized_users_col, "update_one", update_one)
        assert await tokens.authorize(USER, authorized_by=Var.OWNER_ID) is True
        assert calls and calls[0][2] is True, "must upsert"

    async def test_deauthorize_reports_whether_anything_was_removed(
            self, token_enabled, monkeypatch):
        class Removed:
            deleted_count = 1

        async def delete_one(query):
            return Removed()

        monkeypatch.setattr(tokens.db.authorized_users_col, "delete_one", delete_one)
        assert await tokens.deauthorize(USER) is True

        class Nothing:
            deleted_count = 0

        async def delete_nothing(query):
            return Nothing()

        monkeypatch.setattr(tokens.db.authorized_users_col, "delete_one", delete_nothing)
        assert await tokens.deauthorize(USER) is False

    async def test_allowed_reflects_the_database(self, token_enabled, monkeypatch):
        async def found(query, *args, **kwargs):
            return {"_id": "x"}

        monkeypatch.setattr(tokens.db.authorized_users_col, "find_one", found)
        assert await tokens.allowed(USER) is True

        async def missing(query, *args, **kwargs):
            return None

        monkeypatch.setattr(tokens.db.authorized_users_col, "find_one", missing)
        assert await tokens.allowed(USER) is False


class TestListing:
    async def test_list_allowed_returns_empty_on_database_error(
            self, token_enabled, monkeypatch):
        def boom(query, *args, **kwargs):
            raise RuntimeError("database down")

        monkeypatch.setattr(tokens.db.authorized_users_col, "find", boom)
        assert await tokens.list_allowed() == []

    async def test_list_tokens_returns_empty_on_database_error(
            self, token_enabled, monkeypatch):
        def boom(query, *args, **kwargs):
            raise RuntimeError("database down")

        monkeypatch.setattr(tokens.db.token_col, "find", boom)
        assert await tokens.list_tokens() == []

    async def test_list_allowed_propagates_cursor_failure(
            self, token_enabled, monkeypatch):
        """find() is synchronous; a failure while draining the cursor must
        still fall back to an empty list."""
        class FailingCursor:
            async def to_list(self, length=None):
                raise RuntimeError("cursor failed")

        def fake_find(query, projection=None):
            return FailingCursor()

        monkeypatch.setattr(tokens.db.authorized_users_col, "find", fake_find)
        assert await tokens.list_allowed() == []

    async def test_list_allowed_returns_rows(self, token_enabled, monkeypatch):
        class Cursor:
            async def to_list(self, length=None):
                return [{"user_id": 1}, {"user_id": 2}]

        def fake_find(query, projection=None):
            return Cursor()

        monkeypatch.setattr(tokens.db.authorized_users_col, "find", fake_find)
        assert await tokens.list_allowed() == [{"user_id": 1}, {"user_id": 2}]

    async def test_get_user_returns_none_on_error(self, token_enabled, monkeypatch):
        async def boom(query):
            raise RuntimeError("database down")

        monkeypatch.setattr(tokens.db.token_col, "find_one", boom)
        assert await tokens.get_user(USER) is None
