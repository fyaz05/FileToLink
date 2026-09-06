"""Bootstrap a valid fake environment BEFORE any Thunder import."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Hard overrides (not setdefault): must beat any CI/host-leaked env for hermeticity.
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

# Hermeticity: never read the developer's config.env in-process (optional knobs
# like PRIVATE_MODE would leak into assertions); subprocess precedence tests opt back out.
os.environ["THUNDER_SKIP_CONFIG_FILES"] = "1"
