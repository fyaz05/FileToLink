# Thunder/server/exceptions.py


class InvalidHash(Exception):
    pass


class FileNotFound(Exception):
    """The file/record is genuinely gone.  Safe to self-heal a record on."""


class TelegramUnavailable(Exception):
    """Transient Telegram-side failure (FloodWait-exhaustion, timeout,
    transport error).  MUST NOT trigger record self-healing -- raising it
    as FileNotFound made a Telegram brownout delete every vault record
    requested during the outage.  Route handlers map this to 503."""
