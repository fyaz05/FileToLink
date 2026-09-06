#!/usr/bin/env bash
# Boot orchestration only (H9): update is best-effort and shell-free;
# a failing update never blocks the bot from starting.
set -u

python3 update.py || true
exec python3 -m Thunder
