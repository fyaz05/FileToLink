"""Verify _deduplicate_users awaits AsyncCollection.aggregate coroutine on DuplicateKeyError."""
from pymongo.errors import DuplicateKeyError

from Thunder.utils.database import Database


class FakeCursor:
    """Mirrors AsyncCommandCursor: async-iterable once awaited."""

    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()


class FakeDeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class FakeCollection:
    """Mirrors the pymongo async shapes this path uses."""

    def __init__(self, keep_ids=None):
        self._keep_ids = keep_ids or []
        self.deleted_filter = None

    async def aggregate(self, pipeline):
        return FakeCursor([{"_id": doc_id} for doc_id in self._keep_ids])

    async def delete_many(self, filter_):
        self.deleted_filter = filter_
        return FakeDeleteResult(len(self._keep_ids))

    async def create_index(self, *args, **kwargs):
        return "index_name"


class RecoveryCollection(FakeCollection):
    """Fails the unique-id index once, like a collection holding duplicates."""

    def __init__(self, keep_ids):
        super().__init__(keep_ids)
        self.failed_once = False

    async def create_index(self, *args, **kwargs):
        if not self.failed_once and args == ("id",) and kwargs.get("unique"):
            self.failed_once = True
            raise DuplicateKeyError("duplicate key error")
        return "index_name"


def make_db(users_col):
    """A Database with no client: only the collection attributes are used."""
    db = Database.__new__(Database)
    db.col = users_col
    for name in ("banned_users_col", "banned_channels_col", "token_col",
                 "authorized_users_col", "restart_message_col", "files_col",
                 "file_ingest_locks_col"):
        setattr(db, name, FakeCollection())
    return db


async def test_deduplicate_users_awaits_the_aggregate_cursor():
    col = FakeCollection(keep_ids=[10, 20])
    db = make_db(col)

    await db._deduplicate_users()

    assert col.deleted_filter == {"_id": {"$nin": [10, 20]}}


async def test_ensure_indexes_recovers_from_duplicate_keys():
    """Verify ensure_indexes recovers from DuplicateKeyError by running deduplication."""
    col = RecoveryCollection(keep_ids=[10, 20])
    db = make_db(col)

    assert await db.ensure_indexes() is True
    assert col.deleted_filter == {"_id": {"$nin": [10, 20]}}
