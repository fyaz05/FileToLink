.PHONY: format lint test audit coverage run clean

# L8: developer entry points (see CONTRIBUTING.md)

format:
	ruff check Thunder/ update.py --fix
	ruff format Thunder/ update.py

lint:
	ruff check Thunder/ update.py
	mypy Thunder --ignore-missing-imports || true

test:
	pytest -m unit

coverage:
	pytest -m unit --cov=Thunder --cov-report=html

audit:
	pip-audit -r requirements.txt
	bandit -r Thunder -ll --skip B101
	vulture Thunder whitelist.py --min-confidence 80

run:
	python3 -m Thunder

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov **/__pycache__
