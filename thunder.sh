#!/usr/bin/env bash
# Update from UPSTREAM_REPO when configured, then run the bot.
# Python is resolved from this script's directory: a supervisor may start us
# from anywhere, and a system python3 without the dependencies only fails later.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

if [ -x "$HERE/.venv/bin/python" ]; then
    PYTHON="$HERE/.venv/bin/python"
elif [ -x "$HERE/venv/bin/python" ]; then
    PYTHON="$HERE/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    echo "No python3 interpreter found" >&2
    exit 1
fi

cd "$HERE" || exit 1
export PYTHONUNBUFFERED=1

"$PYTHON" update.py || echo "update skipped or failed; starting with current code"
exec "$PYTHON" -m Thunder
