"""Throughput micro-benchmarks (hermetic: no network, no Mongo, no Telegram).

Not wired into CI — numbers vary by machine. Run from the repo root:

    .venv\\Scripts\\python.exe scripts\\bench.py

Compares hot-path costs across refactors; absolute values matter less than
ratios. Keep each bench under a few seconds.
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.update(
    {
        "API_ID": "1",
        "API_HASH": "x" * 32,
        "BOT_TOKEN": "1:abc",
        "BIN_CHANNEL": "-1001",
        "OWNER_ID": "1",
        "DATABASE_URL": "mongodb://localhost:27017/x",
        "THUNDER_SKIP_CONFIG_FILES": "1",
    }
)

from Thunder.server.stream_routes import parse_range_header, validate_public_hash  # noqa: E402
from Thunder.utils.human_readable import humanbytes  # noqa: E402
from Thunder.utils.render_template import render_media_page  # noqa: E402


def bench(name, fn, iters=20000):
    fn()  # warmup
    start = time.perf_counter()
    for _ in range(iters):
        fn()
    dt = time.perf_counter() - start
    print(f"{name:28s} {iters / dt:12,.0f} ops/s")
    return iters / dt


def main() -> None:
    print("Thunder micro-benchmarks (hermetic)")
    bench("parse_range_header", lambda: parse_range_header("bytes=0-1048575", 10 * 1024 * 1024))
    bench("validate_public_hash", lambda: validate_public_hash("a" * 32))
    bench("humanbytes", lambda: humanbytes(10 * 1024 * 1024), iters=50000)

    async def _page():
        return await render_media_page(
            "v.mp4", "https://h/f/x/v.mp4", mime_type="video/mp4", size_bytes=1024
        )

    async def _pages(n=200):
        for _ in range(n):
            await _page()

    start = time.perf_counter()
    asyncio.run(_pages())
    dt = time.perf_counter() - start
    print(f"{'render_media_page':28s} {200 / dt:12,.0f} ops/s")


if __name__ == "__main__":
    main()
