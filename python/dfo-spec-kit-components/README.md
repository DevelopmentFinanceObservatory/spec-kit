# dfo-spec-kit-components (Python)

The presentation contract bundle: the envelope and component JSON Schemas, the write-side intent grammar, and their conformance fixtures, shipped as package data. See the [repo README](../../README.md) for how this package fits with the rest of `dfo-spec-kit`, and `src/dfo_spec_kit_components/catalogue/README.md` for the bundle's own format.

Import name: `dfo_spec_kit_components`. The package is static data plus one accessor function, and stands alone in this repo.

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
uv run ty check src/dfo_spec_kit_components
```

## The catalogue bundle

`src/dfo_spec_kit_components/catalogue/` ships as package data: schemas, intents, and fixtures installed with the package. `dfo_spec_kit_components.bundle_dir()` locates it from either a repo checkout or an installed wheel. See the README beside it for the bundle's format and how to add a schema or fixture.
