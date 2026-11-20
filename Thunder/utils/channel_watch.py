"""Recheck storage-channel access after boot.

A bot removed from its channel later keeps running and fails only when a user
asks for a link; this reports it on the next poll instead. Checks only what can
change without a restart (membership and posting rights), and only transitions,
so a lasting problem produces one message.
"""
import asyncio

from Thunder.utils.logger import logger

HEALTHY = "healthy"
BROKEN = "broken"


class ChannelWatch:
    """Tracks one channel's permission state and reports transitions.

    ``probe`` is an async callable returning a problem description, or None when
    fine. The message is re-returned next poll until ``message_delivered`` confirms
    it, so a Telegram failure cannot silently swallow it.
    """

    def __init__(self, probe):
        self._probe = probe
        self.state = HEALTHY
        self.problem = None
        self._redeliver = False
        self._recovery_pending = None

    def message_undelivered(self):
        """Ask ``check_once`` to return the transition message again.

        A notify failure means the owner never saw it, so retry next poll instead of
        dropping it.
        """
        self._redeliver = True

    def message_delivered(self):
        """Confirm the transition message reached the owner."""
        self._redeliver = False
        if self._recovery_pending is not None:
            self._recovery_pending = None
            self.state = HEALTHY

    async def check_once(self):
        """Run one probe. Return the message to send, or None if nothing to say."""
        try:
            problem = await self._probe()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # A failed probe is not proof of breakage; the network may be down.
            # Keep the current state rather than alerting.
            logger.debug(f"Channel watch probe failed: {type(e).__name__}: {e}")
            return None

        if problem:
            if self.state == BROKEN and not self._redeliver:
                return None  # already reported
            self._recovery_pending = None
            self.state = BROKEN
            self.problem = problem
            return (
                "⚠ I can no longer create links.\n\n"
                f"{problem}\n\n"
                "Links will fail until this is fixed. I will message you again "
                "when access is restored."
            )

        if self.state == BROKEN:
            if self._recovery_pending is not None:
                # The recovery notice is undelivered; re-emit it.
                return self._recovery_pending
            if self._redeliver:
                # The failure message never reached the owner, so a recovery notice
                # would imply something had changed.
                self._redeliver = False
                self.problem = None
                self.state = HEALTHY
                return None
            self._recovery_pending = (
                "✅ Access restored. I can create links again.\n\n"
                f"(Was: {self.problem})"
            )
            self.problem = None
            return self._recovery_pending
        return None


async def watch_channel(notify, probe, interval_seconds):
    """Poll ``probe`` forever and pass transition messages to ``notify``.

    The try belongs inside the loop: an error outside it would kill the watcher for
    the process's life. A failed notify is retried next poll.

    ``interval_seconds`` must be positive; the caller decides whether to start at
    all (``start_services`` does when > 0).
    """
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
