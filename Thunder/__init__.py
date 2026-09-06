# Thunder/__init__.py

import os
import time

StartTime = time.time()

# L8: build-time injectable version (Docker/PaaS may set APP_VERSION; the
# pyproject.toml [project] version is the single source of truth for the
# default).  Exposed by /status and /stats.
__version__ = os.getenv("APP_VERSION", "2.2.0")
