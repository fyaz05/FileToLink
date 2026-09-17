# Thunder/__init__.py

import os
import time

StartTime = time.time()

# L8: build-time injectable version -- Docker/PaaS may set APP_VERSION;
# the pyproject.toml [project] version is the default. Exposed by /status and /stats.
# NOTE: snapshots at import, so env-only (Docker -e / systemd) -- config.env
# loads later in vars.py and cannot set this.
__version__ = os.getenv("APP_VERSION", "2.2.0")
