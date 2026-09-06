# Contributing

## Setup

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov ruff mypy bandit vulture
cp config_sample.env config.env  # fill in your values
```

## Workflow

1. Branch from `main` (`feat/...`, `fix/...`, `refactor/...`).
2. `make lint test` must pass locally; `quality.yml` enforces the same gates.
3. One concern per PR; behavior-preserving refactors carry characterization
   tests committed beforehand.
4. New env vars: safe default + annotated entry in `config_sample.env` in the
   same PR.
5. No new runtime dependency without a one-line justification; the
   dependency-count CI gate fails beyond 8 direct deps.

## Commands

- `make format` — ruff autofix + format
- `make lint` — ruff + mypy (permissive)
- `make test` — unit tier
- `make audit` — pip-audit + bandit + vulture
