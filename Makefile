.PHONY: format lint test coverage audit run clean

# L8: developer entry points (see CONTRIBUTING.md)
# NOTE: recipes MUST be indented with hard TABs, not spaces.

format:
	ruff check Thunder/ update.py --fix
	ruff format Thunder/ update.py

lint:
	ruff check Thunder/ update.py
	mypy Thunder --ignore-missing-imports

test:
	pytest -m unit --cov=Thunder --cov-report=term-missing --cov-fail-under=35

coverage:
	pytest -m unit --cov=Thunder --cov-report=html

audit:
	uv run pip-audit
	bandit -r Thunder -ll --skip B101
	vulture Thunder --min-confidence 80
	@count=$$(grep -cE '^[a-zA-Z0-9_-]+==' requirements.txt); \
	echo "Direct runtime deps: $$count"; \
	if [ "$$count" -gt 8 ]; then \
	        echo "ERROR: dependency count increased beyond 8; justify or remove."; \
	        exit 1; \
	fi

run:
	python3 -m Thunder

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov **/__pycache__
