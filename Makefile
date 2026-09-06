.PHONY: format lint test coverage audit run clean

# L8: developer entry points (see CONTRIBUTING.md)
# Recipes need hard TABs; tools run via `uv run` (project env, not the ambient venv).

format:
	uv run ruff check Thunder/ update.py tests/ --fix
	uv run ruff format Thunder/ update.py tests/

lint:
	uv run ruff check Thunder/ update.py tests/
	uv run mypy Thunder --ignore-missing-imports

test:
	uv run pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35

coverage:
	uv run pytest -m unit --cov=Thunder --cov-report=html

audit:
	uv run pip-audit
	uv run bandit -r Thunder -ll --skip B101
	uv run vulture Thunder --min-confidence 80
	@count=$$(grep -cE '^[a-zA-Z0-9_-]+==' requirements.txt); \
	echo "Direct runtime deps: $$count"; \
	if [ "$$count" -gt 8 ]; then \
		echo "ERROR: dependency count increased beyond 8; justify or remove."; \
		exit 1; \
	fi

run:
	uv run python3 -m Thunder

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov build dist
	find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} +
