"""Report storage-channel access transitions after boot."""
import asyncio

from Thunder.utils.logger import logger

HEALTHY = "healthy"
BROKEN = "broken"


class ChannelWatch:
    """Track observed access separately from the last state delivered to the owner."""

    def __init__(self, probe):
        self._probe = probe
        self.state = HEALTHY
        self.problem = None
        self._last_delivered_state = HEALTHY
        self._pending_state = None
        self._pending_message = None
        self._last_message = None
        self._last_message_state = None

    def message_undelivered(self):
        if self._last_message is not None:
            self._pending_state = self._last_message_state
            self._pending_message = self._last_message

    def message_delivered(self):
        if self._last_message_state is not None:
            self._last_delivered_state = self._last_message_state
        self._pending_state = None
        self._pending_message = None
        if self.state == HEALTHY:
            self.problem = None

    def _message(self, state):
        self._last_message_state = state
        if state == BROKEN:
            self._last_message = (
                "⚠ I can no longer create links.\n\n"
                f"{self.problem}\n\n"
                "Links will fail until this is fixed. I will message you again "
                "when access is restored."
            )
        else:
            self._last_message = (
                "✅ Access restored. I can create links again.\n\n"
                f"(Was: {self.problem})"
            )
        return self._last_message

    async def check_once(self):
        self._last_message = None
        self._last_message_state = None
        try:
            problem = await self._probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A failed or inconclusive probe is not proof of lost or restored access.
            logger.debug(f"Channel watch probe failed: {type(e).__name__}: {e}")
            return None

        if problem:
            self.state = BROKEN
            self.problem = problem
            if self._pending_state == HEALTHY:
                # The owner already knows about the outage; keep the pending recovery.
                return None
            if self._last_delivered_state == BROKEN and self._pending_state != BROKEN:
                return None
            if self._pending_state == BROKEN:
                self._last_message_state = BROKEN
                self._last_message = self._pending_message
                return self._last_message
            return self._message(BROKEN)

        self.state = HEALTHY
        if self._pending_state == BROKEN:
            # The outage was never delivered, so there is no recovery to announce.
            self._pending_state = None
            self._pending_message = None
            self.problem = None
            return None
        if self._pending_state == HEALTHY:
            self._last_message_state = HEALTHY
            self._last_message = self._pending_message
            return self._last_message
        if self._last_delivered_state == BROKEN:
            return self._message(HEALTHY)
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
                        f"the next poll."
                    )
                    watcher.message_undelivered()
                else:
                    watcher.message_delivered()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"Channel watch failed ({type(e).__name__}: {e}). "
                f"Retrying in {interval_seconds}s."
            )
