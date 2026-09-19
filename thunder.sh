#!/usr/bin/env bash
# boot orchestration only: best-effort shell-free update; a failing update never blocks boot.
set -u

# work from the script's directory so manual invocations from any cwd behave
# like the Docker entrypoint
cd "$(dirname "$0")" || exit 1

python3 update.py || true
exec python3 -m Thunder
