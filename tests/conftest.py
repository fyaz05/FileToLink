"""Shared fixtures for the Thunder test suite."""
import os
import sys

# Var validates required configuration at import time, so set placeholders
# before any Thunder module is imported. Tests never reach the network.
os.environ.setdefault("API_ID", "123456")
os.environ.setdefault("API_HASH", "0123456789abcdef0123456789abcdef")
os.environ.setdefault("BOT_TOKEN", "123456:AA" + "x" * 30)
os.environ.setdefault("BIN_CHANNEL", "-1001234567890")
os.environ.setdefault("OWNER_ID", "123456")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017/thunder_test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from Thunder.bot import multi_clients, work_loads  # noqa: E402


@pytest.fixture(autouse=True)
def clean_workloads():
    """Every test starts and ends with an empty workload table."""
    work_loads.clear()
    multi_clients.clear()
    work_loads[0] = 0
    yield
    work_loads.clear()
    multi_clients.clear()
