# Contributing

## Setup

```bash
# install uv (https://docs.astral.sh/uv/getting-started/installation/), then:
uv sync --frozen --group dev   # exact locked env: runtime + dev tools
cp config_sample.env config.env  # fill in your values
pre-commit install  # optional: same ruff hooks CI runs, before each commit
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
6. Touching `pyproject.toml` or `uv.lock`? Run `make lock` -- the same drift
   gates CI enforces (uv.lock, requirements.txt, requirements.lock).

## Commands

- `make format` — ruff autofix + format
- `make lint` — ruff + mypy (blocking: 0 errors expected)
- `make test` — unit tier
- `make integration` — real-Mongo tier (needs Docker)
- `make lock` — dependency drift gates
- `make audit` — pip-audit + bandit + vulture
