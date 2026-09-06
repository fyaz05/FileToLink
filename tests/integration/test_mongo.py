# tests/integration/test_mongo.py
"""L9: integration tier against a real MongoDB via testcontainers.

Run explicitly:  pytest -m integration
Requires Docker; skipped cleanly (as designed, mirroring ThunderGo's
build-tag-gated tier) when Docker is unavailable.
"""

import os
from datetime import UTC

import pytest

pytestmark = pytest.mark.integration

docker_unavailable = True
mongo_uri = None

try:  # pragma: no cover - environment-dependent
    from testcontainers.community.mongodb import MongoDbContainer as MongoContainer

    docker_unavailable = False
except ImportError:
    MongoContainer = None


@pytest.fixture(scope="module")
def mongo_container():
    if docker_unavailable or os.getenv("TEST_INTEGRATION") != "1":
        pytest.skip("integration tier disabled (set TEST_INTEGRATION=1 with Docker)")
    with MongoContainer("mongo:7") as mongo:
        yield mongo


@pytest.fixture(scope="module")
def db(mongo_container):
    import Thunder.utils.database as database_module
    import Thunder.utils.tokens as tokens_module

    # Bind a fresh Database directly to the container URI.  Rebinding is
    # required because Thunder.vars is cached in sys.modules by the unit
    # tier's imports (Var.DATABASE_URL still points at the platform
    # config), and `from ... import db` copies froze the old instance in
    # every consumer module.  Reload-based approaches never worked.
    fresh = database_module.Database(mongo_container.get_connection_url(), "thunder_test")
    original = database_module.db
    database_module.db = fresh
    tokens_module.db = fresh
    try:
        yield fresh
    finally:
        database_module.db = original
        tokens_module.db = original


async def test_ensure_indexes_and_token_atomicity(db):  # pragma: no cover
    assert await db.ensure_indexes(raise_on_error=True) is True

    # M8: atomic activation -- two concurrent consume() calls, one winner
    import asyncio
    from datetime import datetime, timedelta

    from Thunder.utils.tokens import consume

    token = "integration-token-1"
    await db.token_col.insert_one(
        {
            "token": token,
            "user_id": 424242,
            "activated": False,
            "created_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    results = await asyncio.gather(consume(token, 424242), consume(token, 424242))
    statuses = sorted(status for status, _ in results)
    assert statuses == ["already", "ok"]


async def test_ensure_indexes_ttl_lifecycle(  # pragma: no cover
    mongo_container, monkeypatch
):
    """Review item 9 regression: FILE_TTL_DAYS must survive a change between
    boots (IndexOptionsConflict -> drop+recreate) without aborting the
    remaining unique-index ensures, and the legacy-row backfill must stamp
    last_seen_at before the index first activates.

    Uses its own Database instance (fresh event-loop affinity) so it does
    not share the module fixture's client loop.
    """
    from datetime import datetime

    import Thunder.utils.database as database_module
    import Thunder.vars as vars_module

    var = vars_module.Var
    ttl_db = database_module.Database(mongo_container.get_connection_url(), "thunder_ttl_test")

    # legacy row predating any TTL index (no last_seen_at field)
    await ttl_db.files_col.insert_one(
        {
            "file_unique_id": "ttl-legacy-1",
            "public_hash": "f" * 32,
            "canonical_message_id": -77001,
            "created_at": datetime.now(UTC),
        }
    )

    # first boot with TTL enabled: backfill runs, index is created
    monkeypatch.setattr(var, "FILE_TTL_DAYS", 1)
    assert await ttl_db.ensure_indexes(raise_on_error=True) is True
    info = await ttl_db.files_col.index_information()
    assert info["last_seen_at_1"]["expireAfterSeconds"] == 86400
    row = await ttl_db.files_col.find_one({"file_unique_id": "ttl-legacy-1"})
    assert "last_seen_at" in row, "legacy row must be stamped before the TTL index activates"

    # second boot with a CHANGED TTL: recreate must not abort the uniques
    monkeypatch.setattr(var, "FILE_TTL_DAYS", 2)
    assert await ttl_db.ensure_indexes(raise_on_error=True) is True
    info = await ttl_db.files_col.index_information()
    assert info["last_seen_at_1"]["expireAfterSeconds"] == 2 * 86400
    for unique_index in ("file_unique_id_1", "public_hash_1", "canonical_message_id_1"):
        assert unique_index in info, f"unique index {unique_index} must still be ensured"

    # third boot with the SAME TTL: steady state, still green
    assert await ttl_db.ensure_indexes(raise_on_error=True) is True

    await ttl_db.close()


async def test_file_ttl_actually_expires_rows(  # pragma: no cover
    mongo_container, monkeypatch
):
    """End-to-end TTL semantics: a row whose last_seen_at is older than the
    window must eventually disappear once the index is active."""
    from datetime import datetime, timedelta

    import Thunder.utils.database as database_module
    import Thunder.vars as vars_module

    var = vars_module.Var
    ttl_db = database_module.Database(
        mongo_container.get_connection_url(), "thunder_ttl_expire_test"
    )
    await ttl_db.files_col.insert_one(
        {
            "file_unique_id": "ttl-doomed-1",
            "public_hash": "e" * 32,
            "canonical_message_id": -77002,
            "created_at": datetime.now(UTC),
            "last_seen_at": datetime.now(UTC) - timedelta(days=30),
        }
    )

    monkeypatch.setattr(var, "FILE_TTL_DAYS", 1)
    assert await ttl_db.ensure_indexes(raise_on_error=True) is True

    # Mongo's TTL monitor runs roughly once a minute; poll for up to 90s.
    deadline = datetime.now(UTC) + timedelta(seconds=90)
    gone = False
    while datetime.now(UTC) < deadline:
        remaining = await ttl_db.files_col.count_documents({"file_unique_id": "ttl-doomed-1"})
        if remaining == 0:
            gone = True
            break
        import asyncio

        await asyncio.sleep(5)
    assert gone, "TTL monitor did not expire the aged row in time"

    await ttl_db.close()
