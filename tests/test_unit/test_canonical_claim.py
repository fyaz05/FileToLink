"""Ingest-claim locks (fake-backed): acquire / release / steal-expired / owner-check."""

from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from Thunder.utils.database import Database


class _FakeLocksCol:
    """Minimal stand-in for file_ingest_locks_col: insert_one + find_one_and_update."""

    def __init__(self):
        self.store: dict[str, dict] = {}

    async def insert_one(self, doc: dict):
        if doc["_id"] in self.store:
            raise DuplicateKeyError("dup")
        self.store[doc["_id"]] = dict(doc)

    async def find_one_and_update(self, flt: dict, update: dict, **_kwargs):
        from datetime import datetime

        current = self.store.get(flt["_id"])
        if current is None:
            return None
        expires_at = current.get("expires_at")
        if expires_at is not None and expires_at > datetime.now(expires_at.tzinfo):
            return None  # live claim held by someone else
        old = dict(current)
        current.update(update["$set"])
        return old

    async def delete_one(self, flt: dict):
        current = self.store.get(flt["_id"])
        if current is not None and current.get("owner") == flt.get("owner"):
            del self.store[flt["_id"]]
            return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def _make_claim_db() -> Database:
    db = Database.__new__(Database)
    db.file_ingest_locks_col = _FakeLocksCol()  # type: ignore[assignment]
    return db


@pytest.mark.unit
async def test_acquire_release_cycle():
    db = _make_claim_db()
    owner = await db.acquire_file_ingest_claim("file-1")
    assert owner  # opaque owner token
    # live claim blocks a second worker
    assert await db.acquire_file_ingest_claim("file-1") is None
    # owner-checked release frees it
    assert await db.release_file_ingest_claim("file-1", owner) is True
    assert await db.release_file_ingest_claim("file-1", owner) is False
    # free again: a new worker can claim
    assert await db.acquire_file_ingest_claim("file-1")


@pytest.mark.unit
async def test_expired_claim_is_stolen():
    db = _make_claim_db()
    owner = await db.acquire_file_ingest_claim("file-2", ttl_seconds=-1)  # already expired
    thief = await db.acquire_file_ingest_claim("file-2")
    assert thief and thief != owner
    # the old owner's release must not delete the new worker's claim
    assert await db.release_file_ingest_claim("file-2", owner) is False
    assert await db.release_file_ingest_claim("file-2", thief) is True
