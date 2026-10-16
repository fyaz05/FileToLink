"""users.id dedup must scale and must keep the oldest document.

The previous implementation sorted the whole collection in memory and then
deleted with a single ``{"_id": {"$nin": keep_ids}}`` -- one ObjectId per user
inside one BSON document (16 MB cap), which trips the client ``timeoutMS`` on a
large collection and leaves ``users.id`` permanently unindexed.
"""

import pytest

from Thunder.utils.database import Database


class _FakeUsers:
    """Async stand-in for the ``users`` collection."""

    def __init__(self, groups):
        self.groups = groups
        self.pipeline = None
        self.aggregate_kwargs = None
        self.deletes: list[dict] = []

    async def aggregate(self, pipeline, **kwargs):
        self.pipeline = pipeline
        self.aggregate_kwargs = kwargs

        async def _cursor():
            for g in self.groups:
                yield g

        return _cursor()

    async def delete_many(self, flt):
        self.deletes.append(flt)
        return type("R", (), {"deleted_count": 1})()


def _db(users):
    db = Database.__new__(Database)  # no client needed
    db.col = users
    return db


@pytest.mark.unit
async def test_dedup_narrows_to_duplicate_groups_only():
    users = _FakeUsers([{"_id": "tg-1", "n": 2, "keep": "ObjectId(newer)"}])
    await _db(users)._deduplicate_users()

    stages = users.pipeline
    assert {"$match": {"n": {"$gt": 1}}} in stages, "must not walk every user id"
    # server-side spill instead of an in-memory sort over the whole collection
    assert users.aggregate_kwargs.get("allowDiskUse") is True


@pytest.mark.unit
async def test_dedup_deletes_per_group_without_a_nin_array():
    """One ObjectId per user inside a single $nin array is what blew the 16 MB
    BSON document limit; each delete must carry its own small filter."""
    users = _FakeUsers(
        [
            {"_id": "tg-1", "n": 2, "keep": "ObjectId(keep-1)"},
            {"_id": "tg-2", "n": 3, "keep": "ObjectId(keep-2)"},
        ]
    )
    await _db(users)._deduplicate_users()

    assert len(users.deletes) == 2
    for flt in users.deletes:
        assert "$nin" not in flt, f"bulk $nin filter reintroduced: {flt}"
        assert set(flt) == {"id", "_id"}
        assert "$ne" in flt["_id"], "the surviving document must be excluded"


@pytest.mark.unit
async def test_dedup_keeps_the_oldest_by_join_date():
    users = _FakeUsers([{"_id": "tg-1", "n": 2, "keep": "ObjectId(oldest)"}])
    await _db(users)._deduplicate_users()

    assert users.pipeline[0] == {"$sort": {"join_date": 1}}
    group = next(s for s in users.pipeline if "$group" in s)
    assert group["$group"]["keep"] == {"$first": "$_id"}


@pytest.mark.unit
async def test_dedup_is_a_noop_without_duplicates():
    users = _FakeUsers([])
    await _db(users)._deduplicate_users()

    assert users.deletes == []
