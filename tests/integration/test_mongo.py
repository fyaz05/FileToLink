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
def db():
    if docker_unavailable or os.getenv("TEST_INTEGRATION") != "1":
        pytest.skip("integration tier disabled (set TEST_INTEGRATION=1 with Docker)")
    with MongoContainer("mongo:7") as mongo:
        import Thunder.utils.database as database_module
        import Thunder.utils.tokens as tokens_module

        # Bind a fresh Database directly to the container URI.  Rebinding is
        # required because Thunder.vars is cached in sys.modules by the unit
        # tier's imports (Var.DATABASE_URL still points at the platform
        # config), and `from ... import db` copies froze the old instance in
        # every consumer module.  Reload-based approaches never worked.
        fresh = database_module.Database(mongo.get_connection_url(), "thunder_test")
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
