# tests/conftest.py
"""Bootstrap a valid fake environment BEFORE any Thunder import.

Thunder.vars validates at import time and hard-fails on missing required
values (plan H7/M6), so the unit tier must always run with a complete,
hermetic environment -- no network, no Mongo (AsyncMongoClient constructs
lazily and never connects at import).
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Hard overrides (not setdefault): the unit tier must be hermetic even when
# the CI/host environment leaks unrelated DATABASE_URL-style variables.
_required = {
    "API_ID": "1234567",
    "API_HASH": "test-hash",
    "BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
    "BIN_CHANNEL": "-1001234567890",
    "OWNER_ID": "42",
    "DATABASE_URL": "mongodb://localhost:27017/thunder_test",
    "FQDN": "example.com",
    "NO_PORT": "True",
    "LOG_LEVEL": "WARNING",
}
for _key, _value in _required.items():
    os.environ[_key] = _value
