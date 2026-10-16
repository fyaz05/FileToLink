#!/usr/bin/env bash
# Fast developer loop.
#
# Why this exists: importing pyrogram costs ~2.8s and happens on EVERY pytest
# run, so there is a hard floor of ~3.8s per invocation no matter how few
# tests you select. Measured on a Windows host with the dev group synced:
#
#   gates                1.5s   ruff + ruff-format + mypy, no pytest at all
#   file <path>          4.0s   one test file (or one node with "::")
#   quick                6.8s   the whole unit tier, no coverage
#   test                10.7s   the whole tier plus the coverage gate
#
# The expensive part is running the wrong command, not the tool being slow.
# Iterate with `gates` and `file`; run `quick`/`test` ONCE per commit.
#
#   scripts/dev.sh gates
#   scripts/dev.sh file tests/test_unit/test_batch_chunking.py
#   scripts/dev.sh file 'tests/test_unit/test_batch_chunking.py::test_x'
#   scripts/dev.sh quick
#   scripts/dev.sh test
#   scripts/dev.sh all          # what CI runs, minus the slow audits
#
# Equivalent to the Makefile targets, for hosts without `make`.

set -euo pipefail
cd "$(dirname "$0")/.."

gates() {
  uv run ruff check Thunder/ update.py tests/
  uv run ruff format --check Thunder/ update.py tests/
  uv run mypy Thunder update.py --ignore-missing-imports
}

case "${1:-}" in
  gates) gates ;;
  file)
    [ $# -eq 2 ] || { echo "usage: dev.sh file <path-or-node>" >&2; exit 2; }
    uv run pytest -m unit -q --no-cov "$2" ;;
  quick) uv run pytest -m unit -q --no-cov ;;
  test)  uv run pytest -m unit -q --cov=Thunder --cov-report=term-missing --cov-fail-under=35 ;;
  all)   gates; uv run pytest -m unit -q --cov=Thunder --cov-report=term-missing --cov-fail-under=35 ;;
  *)     sed -n '3,21p' "$0"; echo >&2; echo "pick a subcommand (there is no default)" >&2; exit 2 ;;
esac