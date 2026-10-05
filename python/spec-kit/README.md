# dfo-spec-kit (Python)

Executable form of the platform's shared contracts: dataset-spec models and identity canonicalisation. See the [repo README](../../README.md) for what this package is and how it fits with the rest of `dfo-spec-kit`.

## Installation

This package uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run ty check src/dfo_spec_kit
```

## Pre-commit hooks

```bash
pre-commit install
pre-commit run --all-files
```
