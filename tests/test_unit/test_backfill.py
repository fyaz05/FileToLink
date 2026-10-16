"""Review fix regression: the last_seen_at backfill must converge across
boots (persisted cursor + unstamped-only pages), not restart every boot."""

import pytest

from Thunder.utils.database import Database


class _FakeCursor:
    """Cursor stand-in that HONOURS sort().

    The previous version returned self and ignored the key, so removing the
    real `.sort("_id", 1)` from the backfill left every test green -- while the
    paging it exists to make correct depends on exactly that ordering: the next
    boot resumes with `_id: {$gt: last_id}` and an unsorted page would skip
    rows permanently, then still stamp the "converged" marker.

    Docs are returned shuffled, so a caller that forgets to sort fails here the
    way it would fail against a real collection.
    """

    def __init__(self, docs: list[dict]):
        # deliberately not in _id order
        self._docs = list(reversed(docs))
        self._sort_key: str | None = None
        self._descending = False

    def sort(self, key=None, direction=None, **_kwargs):  # noqa: ARG002 - chain shape
        self._sort_key = key
        self._descending = direction == -1
        return self

    async def to_list(self, limit: int) -> list[dict]:
        docs = self._docs
        if self._sort_key is not None:
            docs = sorted(docs, key=lambda d: d[self._sort_key], reverse=self._descending)
        return docs[:limit]


class _FakeFilesCol:
    """Minimal AsyncCollection stand-in: find + update_many only."""

    name = "files"

    def __init__(self, docs: list[dict]):
        self.docs = docs

    def find(self, flt: dict, projection=None):  # noqa: ARG002 - projection is unread
        unstamped_only = (
            isinstance(flt.get("last_seen_at"), dict)
            and flt["last_seen_at"].get("$exists") is False
        )
        gt = flt.get("_id", {}).get("$gt") if isinstance(flt.get("_id"), dict) else None
        out = []
        for d in self.docs:
            if unstamped_only and "last_seen_at" in d:
                continue
            if gt is not None and not d["_id"] > gt:
                continue
            out.append({"_id": d["_id"]})
        return _FakeCursor(out)

    async def update_many(self, flt: dict, update: dict) -> int:
        ids = set(flt["_id"]["$in"])
        n = 0
        for d in self.docs:
            if d["_id"] in ids:
                d["last_seen_at"] = update["$set"]["last_seen_at"]
                n += 1
        return n


class _FakeFlagsCol:
    name = "migration_flags"

    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one(self, flt: dict) -> dict | None:
        return self.docs.get(flt["_id"])

    async def update_one(self, flt: dict, update: dict, upsert: bool = False) -> None:
        doc = self.docs.setdefault(flt["_id"], {"_id": flt["_id"]})
        doc.update(update["$set"])


def _make_db(docs: list[dict]) -> Database:
    # __new__: the backfill path touches only the two collections below
    db = Database.__new__(Database)
    db.files_col = _FakeFilesCol(docs)  # type: ignore[assignment]
    db.migration_flags_col = _FakeFlagsCol()  # type: ignore[assignment]
    return db


@pytest.mark.unit
async def test_backfill_converges_across_boots():
    # 50_600 unstamped rows vs a 50k-per-boot cap: boot 1 must stop at the
    # cap WITHOUT losing its place, boot 2 must finish and mark done
    docs = [{"_id": i} for i in range(50_600)]
    db = _make_db(docs)

    await db._backfill_file_last_seen()
    assert not await db._backfill_done()
    assert sum(1 for d in docs if "last_seen_at" in d) == 50_000
    assert await db._get_backfill_cursor() == 49_999

    await db._backfill_file_last_seen()
    assert await db._backfill_done()
    assert all("last_seen_at" in d for d in docs)


@pytest.mark.unit
async def test_backfill_completes_and_marks_done_in_one_boot():
    docs = [{"_id": i} for i in range(1_200)]  # not a batch-size multiple
    db = _make_db(docs)

    await db._backfill_file_last_seen()
    assert await db._backfill_done()
    assert all("last_seen_at" in d for d in docs)


@pytest.mark.unit
async def test_backfill_skips_already_stamped_rows():
    docs = [{"_id": 0, "last_seen_at": 1}, {"_id": 1}]
    db = _make_db(docs)

    await db._backfill_file_last_seen()
    assert await db._backfill_done()
    # the stamped row keeps its original value; only the legacy row is touched
    assert docs[0]["last_seen_at"] == 1
    assert "last_seen_at" in docs[1]


@pytest.mark.unit
async def test_paging_only_works_because_the_cursor_is_sorted():
    """The cursor is resumed with `_id: {$gt: last_id}` where last_id is the
    last row of the page. Without `.sort("_id", 1)` that is not the maximum, so
    rows are skipped permanently -- and the marker still says "converged".

    _FakeCursor hands back reversed docs, so an unsorted page leaves gaps that
    the second boot cannot reach.
    """
    docs = [{"_id": i} for i in range(1_200)]
    db = _make_db(docs)

    await db._backfill_file_last_seen()
    await db._backfill_file_last_seen()

    assert await db._backfill_done(), "the backfill claims to have converged"
    missing = [d["_id"] for d in docs if "last_seen_at" not in d]
    assert not missing, (
        f"{len(missing)} rows were skipped and will never be revisited "
        f"(first missing: {missing[:5]}) -- the page was not sorted by _id"
    )
