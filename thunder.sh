#!/usr/bin/env bash
# Update from UPSTREAM_REPO when configured, then run the bot.
# Python is resolved from this script's directory: a supervisor may start us
# from anywhere, and a system python3 without the dependencies only fails later.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

# A venv keeps both layouts because this script is used on Windows too, where
# the interpreter is under Scripts. python3 from PATH is the last resort: on
# Windows that name is a Store stub which exits non-zero with an install prompt,
# so the check below verifies the interpreter actually runs.
for candidate in "$HERE/.venv/bin/python" "$HERE/.venv/Scripts/python.exe" \
                 "$HERE/venv/bin/python" "$HERE/venv/Scripts/python.exe"; do
    if [ -x "$candidate" ]; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "${PYTHON:-}" ] && command -v python3 >/dev/null 2>&1 \
        && python3 -c "" >/dev/null 2>&1; then
    PYTHON="python3"
fi

if [ -z "${PYTHON:-}" ]; then
    echo "No working python interpreter found (looked for a venv, then python3)" >&2
    exit 1
fi

cd "$HERE" || exit 1
export PYTHONUNBUFFERED=1

"$PYTHON" update.py || echo "update skipped or failed; starting with current code"
exec "$PYTHON" -m Thunder
