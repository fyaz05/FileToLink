"""Bootstrap a valid fake environment BEFORE any Thunder import."""

import os
import sys
from pathlib import Path

import pytest

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
    "PRIVATE_MODE": "False",
    "TOKEN_ENABLED": "False",
    "SHORTEN_ENABLED": "False",
    "SHORTEN_MEDIA_LINKS": "False",
    "FILE_TTL_DAYS": "0",
    "RATE_LIMIT_ENABLED": "False",
    "GLOBAL_RATE_LIMIT": "False",
}
for _key, _value in _required.items():
    os.environ[_key] = _value

# Hermeticity: never read the developer's config.env in-process (optional knobs
# like PRIVATE_MODE would leak into assertions); subprocess precedence tests opt back out.
os.environ["THUNDER_SKIP_CONFIG_FILES"] = "1"


@pytest.fixture(autouse=True)
def _restore_singletons():
    """Snapshot mutable singletons so one test's tuning can't leak into the next."""
    import copy

    # lazy imports: avoid import cycles at conftest import time
    import Thunder.utils.rate_limiter as _rl_mod
    import Thunder.vars as _vars_mod

    _rl = _rl_mod.rate_limiter
    _saved_rl_dict = copy.copy(_rl.__dict__)
    _saved_user_requests = {k: copy.copy(v) for k, v in _rl.user_requests.items()}
    _saved_global_requests = copy.copy(_rl.global_requests)
    _saved_breaker_dict = copy.copy(_rl.breaker.__dict__)
    _saved_errors_len = len(_vars_mod._config_errors)
    try:
        yield
    finally:
        _rl.__dict__.clear()
        _rl.__dict__.update(_saved_rl_dict)
        _rl.user_requests.clear()
        _rl.user_requests.update(_saved_user_requests)
        _rl.global_requests.clear()
        _rl.global_requests.extend(_saved_global_requests)
        _rl.breaker.__dict__.clear()
        _rl.breaker.__dict__.update(_saved_breaker_dict)
        del _vars_mod._config_errors[_saved_errors_len:]
