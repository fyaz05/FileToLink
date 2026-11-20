#!/usr/bin/env bash
# Update from UPSTREAM_REPO when configured, then run the bot.
# Python is resolved relative to this script: a supervisor may start us anywhere,
# and a system python3 lacking dependencies only fails later.
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

# Check both venv layouts (Windows uses Scripts). python3 from PATH is a last resort:
# on Windows it is a Store stub that exits non-zero with an install prompt, so the
# check below verifies the interpreter actually runs.
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
# "$@" matters: `--check` validates config without starting anything; without it
# the flag is unreachable in deployments that use this script.
exec "$PYTHON" -m Thunder "$@"
