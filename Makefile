.PHONY: format lint test integration coverage lock audit run clean

# L8: developer entry points (see CONTRIBUTING.md)
# Recipes need hard TABs; tools run via `uv run` (project env, not the ambient venv).

format:
	uv run ruff check Thunder/ update.py tests/ --fix
	uv run ruff format Thunder/ update.py tests/

lint:
	uv run ruff check Thunder/ update.py tests/
	uv run mypy Thunder update.py --ignore-missing-imports

test:
	uv run pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35

# CI parity for the opt-in tier (needs Docker): real MongoDB via testcontainers
integration:
	TEST_INTEGRATION=1 uv run pytest -m integration

coverage:
	uv run pytest -m unit --cov=Thunder --cov-report=html

audit:
	uv run pip-audit
	uv run bandit -c pyproject.toml -r Thunder update.py -ll --skip B101
	uv run vulture Thunder update.py --min-confidence 80
	@count=$$(grep -cE '^[a-zA-Z0-9_-]+==' requirements.txt); \
	echo "Direct runtime deps: $$count"; \
	if [ "$$count" -gt 8 ]; then \
		echo "ERROR: dependency count increased beyond 8; justify or remove."; \
		exit 1; \
	fi

# same drift gates CI enforces; run after touching pyproject/uv.lock
lock:
	uv lock --check
	uv run python -c "import sys, tomllib; deps={d.strip() for d in tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']}; listed={l.strip() for l in open('requirements.txt') if l.strip() and not l.startswith('#')}; sys.exit('requirements.txt drifted from pyproject [project.dependencies]') if deps != listed else None"
	uv export --frozen --no-dev --hashes -o /tmp/requirements.lock.check
	grep -v '^#' requirements.lock > /tmp/requirements.lock.committed
	grep -v '^#' /tmp/requirements.lock.check > /tmp/requirements.lock.exported
	diff -u /tmp/requirements.lock.committed /tmp/requirements.lock.exported

run:
	uv run python3 -m Thunder

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov build dist
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
