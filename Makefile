.PHONY: format lint test integration coverage lock audit run clean gates quick t

# Developer entry points (see CONTRIBUTING.md)
# Recipes need hard TABs; tools run via `uv run` (project env, not the ambient venv).

format:
	uv run ruff check Thunder/ update.py tests/ --fix
	uv run ruff format Thunder/ update.py tests/

lint:
	uv run ruff check Thunder/ update.py tests/
	uv run ruff format --check Thunder/ update.py tests/
	uv run mypy Thunder update.py --ignore-missing-imports

test:
	uv run pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35

# --- the fast loop -------------------------------------------------------
#
# Measured with the dev group synced. Importing pyrogram costs ~2.8s and
# happens in every pytest run, so there is a hard floor of ~3.8s per
# invocation no matter how few tests you select. The expensive part is
# therefore running the WRONG command, not the tool being slow:
#
#   make gates      1.5s  ruff + ruff-format + mypy, no pytest
#   make t <path>   4.0s  one test file (or one node with ::)
#   make quick      6.8s  the whole unit tier, no coverage
#   make test      10.7s  the whole tier plus the coverage gate
#
# Iterate with `make gates` and `make t <file>`. Run `make quick` or
# `make test` ONCE per commit, not after every edit.

gates:
	uv run ruff check Thunder/ update.py tests/
	uv run ruff format --check Thunder/ update.py tests/
	uv run mypy Thunder update.py --ignore-missing-imports

# make t FILE=tests/test_unit/test_batch_chunking.py
# make t FILE=tests/test_unit/test_batch_chunking.py::test_chunking_never_drops
t:
	uv run pytest -m unit -q --no-cov $(FILE)

# the whole tier without the coverage gate -- the pre-commit check
quick:
	uv run pytest -m unit -q --no-cov

# CI parity for the opt-in tier (needs Docker): real MongoDB via testcontainers
integration:
	TEST_INTEGRATION=1 uv run pytest -m integration

coverage:
	uv run pytest -m unit --cov=Thunder --cov-report=html

audit:
	uv run pip-audit
	# -ll = medium severity and above (bandit counts: -l low, -ll medium, -lll high)
	uv run bandit -c pyproject.toml -r Thunder update.py -ll --skip B101
	uv run vulture Thunder update.py --min-confidence 80
	@count=$$(grep -vE '^[[:space:]]*(#|$$)' requirements.txt \
		| sed -E 's/[[:space:]]*;.*$$//; s/[[:space:]]*\[.*\]//; s/==.*$$//' \
		| tr -d '\r' | sort -u | wc -l); \
	echo "Direct runtime deps: $$count"; \
	if [ "$$count" -gt 7 ]; then \
		echo "ERROR: dependency count increased beyond 7; justify or remove."; \
		exit 1; \
	fi

# same drift gates CI enforces; run after touching pyproject/uv.lock
lock:
	uv lock --check
	uv run --locked python -c "import sys, tomllib; deps={d.strip() for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']}; listed={l.strip() for l in open('requirements.txt') if l.strip() and not l.startswith('#')}; sys.exit('requirements.txt drifted from pyproject [project.dependencies]') if deps != listed else None"
	uv export --frozen --no-dev --hashes -o /tmp/requirements.lock.check
	grep -v '^#' requirements.lock > /tmp/requirements.lock.committed
	grep -v '^#' /tmp/requirements.lock.check > /tmp/requirements.lock.exported
	diff -u /tmp/requirements.lock.committed /tmp/requirements.lock.exported

run:
	uv run python -m Thunder

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov build dist
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
