#!/usr/bin/env bash
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"

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
exec "$PYTHON" -m Thunder "$@"
