# Contributing to Liquid

Thank you for your interest in contributing! Liquid is an open-source project and we welcome contributions of all kinds.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup

```bash
git clone https://github.com/ertad-family/liquid.git
cd liquid
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
.venv/bin/pre-commit install
```

### Run Tests

```bash
pytest tests/ -v
```

### Lint & Format

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## How to Contribute

### Reporting Bugs

Open a [bug report](https://github.com/ertad-family/liquid/issues/new?template=bug_report.md) with:
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS

### Suggesting Features

Open a [feature request](https://github.com/ertad-family/liquid/issues/new?template=feature_request.md) describing:
- The use case
- Proposed API/behavior
- Alternatives considered

### Good First Issues

New to the project? Check issues labeled [`good first issue`](https://github.com/ertad-family/liquid/labels/good%20first%20issue) — they're designed to be approachable without deep knowledge of the codebase.

### Pull Requests

1. Fork the repo and create a branch from `develop`:
   ```bash
   git checkout -b feature/my-change develop
   ```
2. Make your changes
3. Add tests for new functionality
4. Ensure all tests pass: `pytest tests/ -v`
5. Ensure lint passes: `ruff check src/ tests/`
6. Commit with a clear message
7. Push and open a PR targeting `develop` (not `main`)

## Code Conventions

- **Async-first**: use `async def` for I/O-bound operations
- **Type hints**: required on all public APIs
- **Pydantic**: for data models
- **Protocols**: for extension points (not ABC)
- **Line length**: 120 characters
- **Tests**: pytest + pytest-asyncio

## Project Structure

```
src/liquid/
    client.py          — Main orchestrator (Liquid class)
    protocols.py       — Extension point interfaces
    exceptions.py      — Error hierarchy
    events.py          — Event system
    _defaults.py       — In-memory implementations for testing
    models/            — Pydantic data models
    discovery/         — API discovery strategies
    auth/              — Auth classification and management
    mapping/           — Field mapping (AI + human review)
    sync/              — Deterministic sync engine
```

## Git Workflow

We follow GitFlow:
- `main` — releases only
- `develop` — integration branch
- `feature/*` — new features
- `fix/*` — bug fixes
- PRs target `develop`, never `main`

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you agree to uphold this code.
