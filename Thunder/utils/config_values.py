"""Parse config without importing Thunder; collect all problems with usable defaults."""

import ipaddress
import os
import re
from typing import List, Optional, Sequence, Set, Tuple

PROBLEMS: List[str] = []
CORRECTIONS: List[str] = []

HEADER = "╠═════════════════ CHECKING CONFIGURATION ══════════════════╣"
FOOTER = "╚═══════════════════════════════════════════════════════════╝"

CONFIG_FILE = "config.env"

_TRUTHY = {"true", "1", "yes", "t", "y", "on", "enabled"}
_FALSY = {"false", "0", "no", "f", "n", "off", "disabled"}
_SCHEME = re.compile(r"^(?:https?|ftp)://", re.IGNORECASE)
_SEPARATORS = re.compile(r"[,\s;]+")


class ConfigError(ValueError):
    """Aggregate problems; ValueError keeps update.py probes compatible."""

    def __init__(self, problems: Sequence[str], corrections: Sequence[str] = ()):
        self.problems = list(problems)
        self.corrections = list(corrections)
        super().__init__("; ".join(self.problems))


def int_env(name: str, default: int, *, required: bool = False,
            minimum: Optional[int] = None,
            maximum: Optional[int] = None,
            expectation: str = "") -> int:
    raw = os.getenv(name, "")
    text = raw.strip()
    if not text:
        if required:
            PROBLEMS.append(f"{name} is not set")
        return default
    if not re.fullmatch(r"-?[0-9]+", text):
        PROBLEMS.append(f"{name} must be a whole number, not {text!r}")
        return default
    value = int(text)
    limit = None
    if minimum is not None and value < minimum:
        limit = f"at least {minimum}"
    elif maximum is not None and value > maximum:
        limit = f"at most {maximum}"
    if limit is not None:
        PROBLEMS.append(f"{name} {expectation or f'must be {limit}'}, not {value}".strip())
        return default
    return value


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip().strip("\"'").lower()
    if text in _TRUTHY or text in _FALSY:
        if raw != raw.strip() or raw.strip().lower() != text:
            CORRECTIONS.append(name)
        return text in _TRUTHY
    PROBLEMS.append(f"{name} must be True or False, not {raw.strip()!r}")
    return default


def str_env(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    cleaned = raw.strip().strip("\"'")
    if cleaned != raw:
        CORRECTIONS.append(name)
    return cleaned


def int_set_env(name: str) -> Set[int]:
    """Parse comma/semicolon-separated IDs without silently discarding them."""
    raw = os.getenv(name, "")
    if not raw.strip():
        return set()
    result: Set[int] = set()
    rejected: List[str] = []
    for token in _SEPARATORS.split(raw.strip()):
        if not token:
            continue
        try:
            result.add(int(token))
        except ValueError:
            rejected.append(token)
    if rejected:
        PROBLEMS.append(f"{name} has entries that are not numbers: {', '.join(rejected)}")
    if "," in raw or ";" in raw:
        CORRECTIONS.append(f"{name} (commas and semicolons read as separators)")
    return result


def mongo_uri_problem(uri: str) -> Optional[str]:
    """Return a safe MongoDB URI syntax error without opening a connection."""
    if not uri:
        return None
    try:
        # This is the same no-I/O parser AsyncMongoClient uses in its
        # constructor, including SRV syntax validation without DNS lookups.
        from pymongo.uri_parser_shared import _validate_uri

        _validate_uri(uri, validate=True, warn=False, normalize=False)
    except Exception as exc:
        return (
            f"DATABASE_URL is malformed ({type(exc).__name__}); "
            "use a mongodb:// or mongodb+srv:// connection string"
        )
    return None


def _valid_port(text: str) -> bool:
    # str.isdigit() accepts characters such as ² that int() rejects, and an
    # arbitrarily long digit string can hit Python's integer-conversion limit.
    return re.fullmatch(r"[0-9]{1,5}", text) is not None and 0 < int(text) <= 65535


def is_valid_host(host: str) -> bool:
    if not host:
        return True
    if host.startswith("["):
        if not host.endswith("]") or "%" in host:
            return False
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError:
            return False
        return True

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    candidate = ascii_host[:-1] if ascii_host.endswith(".") else ascii_host
    if not candidate or len(candidate) > 253:
        return False
    for label in candidate.split("."):
        if not 1 <= len(label) <= 63 or label.startswith("-") or label.endswith("-"):
            return False
        if not all(char.isalnum() or char in "-_" for char in label):
            return False
    return True


def normalise_host(raw: str) -> Tuple[str, Optional[int], List[str]]:
    """Split host/port; return invalid input unchanged for aggregate reporting."""
    notes: List[str] = []
    text = raw.strip().strip("\"'")
    if text != raw.strip():
        notes.append("quotes and spaces")
    while _SCHEME.match(text):
        text = _SCHEME.sub("", text, count=1)
        notes.append("scheme")
    without_slash = text.rstrip("/")
    if without_slash != text:
        notes.append("trailing slash")
    text = without_slash

    if any(char in text for char in "/\\?#@") or any(char.isspace() for char in text):
        return text, None, notes

    if text.startswith("["):
        closing = text.find("]")
        if closing < 0:
            return text, None, notes
        try:
            ipaddress.IPv6Address(text[1:closing])
        except ValueError:
            return text, None, notes
        remainder = text[closing + 1:]
        if not remainder:
            return text[:closing + 1], None, notes
        if not remainder.startswith(":") or not _valid_port(remainder[1:]):
            return text, None, notes
        return text[:closing + 1], int(remainder[1:]), notes

    host, separator, port = text.partition(":")
    if not is_valid_host(host):
        return text, None, notes
    if separator and not _valid_port(port):
        return text, None, notes
    return host, (int(port) if separator else None), notes


def render_config_error(exc: ConfigError, config_found: bool = True) -> str:
    """Distinguish a missing config file from invalid values."""
    lines = [HEADER]
    path = os.path.abspath(CONFIG_FILE)
    if config_found:
        lines.append(f"   config.env: {path}")
    else:
        lines.append("   config.env: NOT FOUND")
        lines.append(f"   Looked in: {path}")
        lines.append("   Values below came from the environment only.")
    lines.append("")
    for problem in exc.problems:
        lines.append(f"   ✖ {problem}")
    if exc.corrections:
        lines.append("")
        lines.append(f"   ◎ Corrections: {', '.join(sorted(set(exc.corrections)))}.")
    lines.append("")
    lines.append(f"   ▶ {len(exc.problems)} problem{'s' if len(exc.problems) != 1 else ''} in configuration.")
    lines.append("   ✖ The bot did not start. Fix all of the above, then start it again.")
    lines.append(FOOTER)
    return "\n".join(lines)
