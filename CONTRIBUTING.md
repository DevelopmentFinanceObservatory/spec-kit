# Contributing

Issues and pull requests are welcome. The ONE Data team maintains this repository. For a large change, such as a new contract, a schema change, or a new package, open an issue first so the design can be discussed before you write it.

Please follow the [Code of Conduct](https://github.com/DevelopmentFinanceObservatory/.github/blob/main/CODE_OF_CONDUCT.md). Report security problems through GitHub private vulnerability reporting (the "Report a vulnerability" button under the Security tab, see the [security policy](https://github.com/DevelopmentFinanceObservatory/.github/blob/main/SECURITY.md)), and keep them out of public issues. For questions, see [SUPPORT.md](https://github.com/DevelopmentFinanceObservatory/.github/blob/main/SUPPORT.md).

## Setup

Everything runs locally and needs no access to ONE's Google Cloud project. Install [uv](https://docs.astral.sh/uv/) and clone the repository. Each package under `python/` is its own uv project with a committed `uv.lock`, so work from inside the package directory:

```bash
cd python/spec-kit
uv sync
```

The six packages are `spec-kit`, `frame-grammar`, `dfo-spec-kit-components`, `provenance-docs`, `descriptions`, and `client-contract`. Python 3.12 or later is required, and CI also runs 3.13.

## Checks before a pull request

Run these in each package you changed. They are the commands CI runs.

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src/<import_name>
```

`<import_name>` is `dfo_spec_kit`, `dfo_frame_grammar`, `dfo_spec_kit_components`, `dfo_provenance_docs`, `dfo_descriptions`, or `dfo_client_contract`. `uv run ruff format .` rewrites files to the formatter's style. Each package also has a `.pre-commit-config.yaml`. `pre-commit install` runs the same linters, plus codespell, actionlint, and mdformat, on each commit.

### Conformance vectors and registries

The validators under `tools/` check the registries and vector suites without importing the packages, so they test the fixtures independently of the implementations. Run them from the repository root. Each is a self-contained script and uv installs its dependencies.

```bash
uv run tools/validate_registry.py
uv run tools/validate_unit_registry.py
uv run tools/validate_vectors.py
uv run tools/validate_frame_vectors.py
uv run tools/validate_unit_vectors.py
uv run tools/validate_provenance_docs_vectors.py
uv run tools/validate_descriptions_vectors.py
uv run tools/validate_client_contract.py
uv run --with jsonschema --with pyarrow --with pytest pytest tools/tests -q
```

The last command tests the validators themselves. [tools/README.md](tools/README.md) says what each validator checks. Each package's tests drive its implementation against the same vectors or fixtures, and `uv run pytest` runs them.

A change to `registries/codes.json` or `registries/units.json` also needs the packaged mirror under `python/spec-kit/src/dfo_spec_kit/` to match byte for byte. The validators fail if the two differ. Edit the file under `registries/` and copy it to the mirror. [registries/README.md](registries/README.md) has the format.

## Branches and commits

Open pull requests against `dev`, the default branch. CI runs on pull requests and pushes to `dev` and `main`, and skips draft pull requests until they are marked ready for review. Commit messages follow Conventional Commits, for example `ci: run spec-kit CI on dev and main`.

## What a pull request needs

- Passing CI, meaning the lint, test, and validator jobs in `.github/workflows/ci.yml`.
- Tests for new behaviour. A change to a contract updates its vectors in the same pull request.
- Updated docs. A change to a package's public behaviour adds an entry under `Unreleased` in that package's `CHANGELOG.md`.
- Compliance with [policies/](policies/README.md), which says which change to which artifact needs which version bump.

Maintainer notes, such as how the packages depend on one another and how releases are checked, are in [docs/development.md](docs/development.md).
