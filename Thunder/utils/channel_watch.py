"""Report storage-channel access transitions after boot."""
import asyncio

from Thunder.utils.logger import logger

HEALTHY = "healthy"
BROKEN = "broken"


class ChannelWatch:
    """Track permission state and retry undelivered transition messages."""

    def __init__(self, probe):
        self._probe = probe
        self.state = HEALTHY
        self.problem = None
        self._retry = False

    def message_undelivered(self):
        self._retry = True

    def message_delivered(self):
        self._retry = False
        if self.state == HEALTHY:
            self.problem = None

    async def check_once(self):
        try:
            problem = await self._probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A failed probe is not proof of lost access.
            logger.debug(f"Channel watch probe failed: {type(e).__name__}: {e}")
            return None

        if problem:
            if self.state == BROKEN and not self._retry:
                return None
            self.state = BROKEN
            self.problem = problem
            self._retry = False
            return (
                "⚠ I can no longer create links.\n\n"
                f"{problem}\n\n"
                "Links will fail until this is fixed. I will message you again "
                "when access is restored."
            )

        if self.state == BROKEN:
            if self._retry:
                self.state = HEALTHY
                self.problem = None
                self._retry = False
                return None
            self.state = HEALTHY
            self._retry = True

        if self._retry:
            return (
                "✅ Access restored. I can create links again.\n\n"
                f"(Was: {self.problem})"
            )
        return None


async def watch_channel(notify, probe, interval_seconds):
    """Poll for transitions and retry failures; caller skips non-positive intervals."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be > 0; 0 disables the watcher")
    watcher = ChannelWatch(probe)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            message = await watcher.check_once()
            if message:
                try:
                    await notify(message)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"Channel watch could not notify the owner "
                        f"({type(e).__name__}: {e}). Will retry the message on "
                        f"the next poll.")
                    watcher.message_undelivered()
                else:
                    watcher.message_delivered()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"Channel watch failed ({type(e).__name__}: {e}). "
                f"Retrying in {interval_seconds}s.")
