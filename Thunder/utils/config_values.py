"""Parse values, collecting every problem instead of raising on the first.

Problems go into ``PROBLEMS`` with a usable default kept, so one restart reports
everything. Imports nothing from Thunder so a broken config can be reported
before any client or server exists.
"""

import os
import re
from typing import List, Optional, Sequence, Set, Tuple

PROBLEMS: List[str] = []
CORRECTIONS: List[str] = []

HEADER = "╠═════════════════ CHECKING CONFIGURATION ══════════════════╣"
FOOTER = "╚═══════════════════════════════════════════════════════════╝"

# Shared by vars and __main__: the config file every stage reads or probes.
CONFIG_FILE = "config.env"

_TRUTHY = {"true", "1", "yes", "t", "y", "on", "enabled"}
_FALSY = {"false", "0", "no", "f", "n", "off", "disabled"}
_SCHEME = re.compile(r"^(?:https?|ftp)://", re.IGNORECASE)
_SEPARATORS = re.compile(r"[,\s;]+")


class ConfigError(ValueError):
    """All configuration problems found, in one exception.

    Subclasses ValueError so update.py's import probes and the tests still see
    a bad config as a ValueError.
    """

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
        if expectation:
            PROBLEMS.append(f"{name} {expectation}, not {value}".strip())
        else:
            PROBLEMS.append(f"{name} must be {limit}, not {value}")
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
    """Parse an id list, accepting commas and semicolons as separators.

    A comma-separated value used to yield an empty set, silently unbanning
    everything the operator meant to block.
    """
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


def normalise_host(raw: str) -> Tuple[str, Optional[int], List[str]]:
    """Reduce a host or URL to bare host and optional port.

    Stripping one scheme is not enough: ``https://https://x`` would collapse to
    ``https:////x`` and fail differently, so schemes are removed in a loop.
    """
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

    if text.startswith("["):
        return text, None, notes
    host, separator, port = text.partition(":")
    if separator and not (port.isdigit() and 0 < int(port) <= 65535):
        return text, None, notes
    return host, (int(port) if port else None), notes


def render_config_error(exc: ConfigError, config_found: bool = True) -> str:
    """The block printed when configuration cannot be parsed.

    ``config_found`` separates "filled in wrongly" from "no config.env here",
    which look identical from missing values alone.
    """
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

