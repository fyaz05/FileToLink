#!/usr/bin/env bash
# (H9) boot orchestration only: best-effort shell-free update; a failing update never blocks boot.
set -u

python3 update.py || true
exec python3 -m Thunder
