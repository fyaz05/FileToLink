"""SIGTERM must actually reach a live interpreter and let it run its finally block.

The unit tier can only assert that thunder.sh says ``exec``. This proves the
other half of the chain for real: pyrofork's ``idle()`` installs a SIGTERM
handler, so a container stop reaches the app's graceful teardown instead of
being ignored by PID 1.

No Telegram credentials are needed -- idle() only waits for a signal.
POSIX only (Windows has no SIGTERM); the integration job runs on Linux.
"""

import os
import signal
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.integration

_CHILD = textwrap.dedent(
    """
    import asyncio, sys
    from pyrogram import idle

    async def main():
        print("ready", flush=True)
        try:
            await idle()
        finally:
            # this is the shape shutdown_services relies on
            print("graceful", flush=True)

    asyncio.run(main())
    """
)


@pytest.mark.integration
@pytest.mark.skipif(os.name == "nt", reason="POSIX signals only")
def test_sigterm_reaches_idle_and_runs_the_teardown():
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready", "child never reached idle()"
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired as e:  # pragma: no cover - failure path
        proc.kill()
        raise AssertionError("child ignored SIGTERM: PID 1 would not forward it") from e

    assert "graceful" in out, f"teardown did not run; stderr: {err[-500:]}"
    assert proc.returncode == 0, f"child exited {proc.returncode}: {err[-500:]}"
