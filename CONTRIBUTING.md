# Contributing

Thanks for your interest in contributing!

## Development setup

```bash
git clone https://github.com/dcgudeman/aiohttp-session-firestore.git
cd aiohttp-session-firestore
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running checks

```bash
ruff check .           # lint
ruff format --check .  # format check
mypy aiohttp_session_firestore  # type check
pytest --cov           # tests + coverage
```

## Submitting changes

1. Fork the repository and create a feature branch.
2. Make your changes and ensure all checks pass.
3. Add or update tests for any new behavior.
4. Open a pull request with a clear description of the change.

## Code style

- Python 3.12+ type hints (use `list`, `dict`, `type | None` — not `Optional`).
- Formatted and linted with [Ruff](https://docs.astral.sh/ruff/).
- Type-checked with [mypy](https://mypy-lang.org/) in strict mode.

## Reporting issues

Please open a [GitHub issue](https://github.com/dcgudeman/aiohttp-session-firestore/issues)
with a clear description, reproduction steps, and your environment details.
