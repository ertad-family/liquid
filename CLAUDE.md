# Liquid

## Project Overview

Python library for programmatic API discovery and adapter generation. AI discovers APIs once, then deterministic code syncs data without LLM calls.

## Tech Stack

- Python 3.12+
- Package manager: uv
- Build backend: hatchling
- Linter/formatter: ruff
- Tests: pytest + pytest-asyncio
- Pre-commit: ruff lint + ruff format

## Project Structure

```
src/liquid/       — library source code
tests/            — pytest tests
docs/             — architecture and design docs
```

## Development Commands

```bash
# Create/activate venv
uv venv .venv && source .venv/bin/activate

# Install with dev deps
uv pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## Git Workflow

GitFlow: `main` (releases) + `develop` (integration). Feature branches from `develop`.

- Branch naming: `feature/<name>`, `fix/<name>`, `release/<version>`
- PRs target `develop`, not `main`
- `main` receives merges only from `develop` via release branches

## Code Conventions

- src layout (`src/liquid/`)
- Async-first: use `async def` for I/O-bound operations
- Type hints on all public APIs
- Pydantic for data models
- Protocols for extension points (Vault, LLM, DataSink)
