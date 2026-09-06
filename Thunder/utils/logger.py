# Thunder/utils/logger.py

import atexit
import json
import logging
import os
import queue
import re
import sys
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.txt")

logging._srcfile = None
logging.logThreads = False
logging.logProcesses = False

# --------------------------------------------------------------------------
# H10: shared secret redaction -- used by the access-log middleware and by
# /log before upload so no token / Mongo URI can leave the machine.
# --------------------------------------------------------------------------

BOT_TOKEN_PATTERN = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35,}")
MONGO_URI_PATTERN = re.compile(r"mongodb(\+srv)?://[^:]+:[^@]+@")
SESSION_TOKEN_PATTERN = re.compile(r"(?i)(authorization:\s*)(Bearer\s+)?[A-Za-z0-9._\-]{20,}")
# API_HASH assignments (32-hex value; contextual so file hashes still log)
API_HASH_PATTERN = re.compile(r"(?i)(api_hash['\"]?\s*[:=]\s*['\"]?)([0-9a-f]{32})")
# pyrogram session strings (long base64url blobs assigned to session vars)
SESSION_STRING_PATTERN = re.compile(
    r"(?i)(session_string['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9_-]{40,})"
)
# activation tokens in t.me deep links (?start=<43-char urlsafe token>)
ACTIVATION_TOKEN_PATTERN = re.compile(r"(\?start=)([A-Za-z0-9_-]{43})")

REDACTED = "***REDACTED***"


def redact_secrets(text: str) -> str:
    """Strip bot tokens, Mongo credentials, API hashes, session strings and
    activation tokens from a log payload."""
    if not text:
        return text
    text = BOT_TOKEN_PATTERN.sub(REDACTED, text)
    text = MONGO_URI_PATTERN.sub("mongodb://***:***@", text)
    text = SESSION_TOKEN_PATTERN.sub(r"\1\2" + REDACTED, text)
    text = API_HASH_PATTERN.sub(r"\1" + REDACTED, text)
    text = SESSION_STRING_PATTERN.sub(r"\1" + REDACTED, text)
    text = ACTIVATION_TOKEN_PATTERN.sub(r"\1" + REDACTED, text)
    return text


def hash_path_token(token: str) -> str:
    """Stable short pseudonym for a file token in access logs."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8", "ignore")).hexdigest()[:8]


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, redact: bool = True):
        super().__init__(fmt)
        self._redact = redact

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if self._redact:
            message = redact_secrets(message)
        return message


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "name": record.name,
            "msg": redact_secrets(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
_log_format = os.getenv("LOG_FORMAT", "plain").lower()

log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=10000)

if _log_format == "json":
    file_formatter: logging.Formatter = JsonFormatter()
    console_formatter: logging.Formatter = JsonFormatter()
else:
    plain = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_formatter = RedactingFormatter(plain)
    console_formatter = RedactingFormatter(plain)

file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(file_formatter)

console_handler = logging.StreamHandler(stream=sys.__stdout__)
console_handler.setFormatter(console_formatter)
# reconfigure exists on io.TextIOWrapper (the real sys.__stdout__ under
# CPython); guard so wrapped/replaced streams can never crash boot.
_stream = console_handler.stream
if hasattr(_stream, "reconfigure"):
    _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

listener = QueueListener(log_queue, file_handler, console_handler, respect_handler_level=True)
listener.start()

logger = logging.getLogger("ThunderBot")
logger.setLevel(_log_level)
logger.propagate = False
logger.addHandler(QueueHandler(log_queue))

atexit.register(listener.stop)

__all__ = ["logger", "LOG_FILE", "redact_secrets", "hash_path_token"]
