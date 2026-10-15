# DFO spec kit

The spec kit holds the shared data contracts of the Development Finance Observatory as JSON schemas, registries, conformance vectors, and Python packages that implement them. Services that must agree on the same bytes, ids, and document formats import them from here instead of keeping their own copies.

It is part of the [Development Finance Observatory](https://github.com/DevelopmentFinanceObservatory), an initiative of [ONE Data](https://data.one.org) built on [Google Data Commons](https://datacommons.org). The org profile has the map of all repositories. The services that consume the kit are `apis` (the Search API), `ai` (the harness and Analysis MCP), `website`, and `docs`.

This repository is under active development. Interfaces, schemas, and data may change without notice.

## What the kit is for

Each contract answers one question that two or more services must answer identically.

- Which dataset is this request asking for? A dataset spec is reduced to a canonical form and hashed into a `spec_id`, so every service mints the same id for the same request.
- Has this table of observations changed? A materialised frame is serialised to canonical bytes and hashed into a `content_version`.
- How do units compare? A unit ref reads as a typed value (money with a currency and price basis, a percent, a count), and refuses comparisons that would mix price bases.
- What does a card or chart look like on the wire? A catalogue of JSON schemas describes each presentation component.
- How is a result documented? Step records and descriptions say what was done to a result, and provenance documents carry transformations, changelogs, and methodology.

Every contract ships with conformance vectors, language-neutral fixtures that any implementation, in any language, can run against. Where prose and vectors disagree, the vectors win.

## Packages

Five Python distributions live under `python/`. Each has its own version, dependencies, tests, and README. All need Python 3.12 or later.

| Distribution                   | Import name               | What it does                                                                                                              |
| ------------------------------ | ------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `dfo-spec-kit`                 | `dfo_spec_kit`            | Dataset-spec identity (canonical form, `spec_id`) and typed units read from `registries/units.json`.                      |
| `dfo-spec-kit-frame-grammar`   | `dfo_frame_grammar`       | Canonical frame bytes and `content_version` for a materialised observation table.                                         |
| `dfo-spec-kit-components`      | `dfo_spec_kit_components` | The presentation catalogue: envelope and component schemas, the write-side intent grammar, and fixtures, as package data. |
| `dfo-spec-kit-provenance-docs` | `dfo_provenance_docs`     | Decoder and parsers for the Markdown documents a Provenance node carries.                                                 |
| `dfo-spec-kit-descriptions`    | `dfo_descriptions`        | Step and description schemas, the operation list, and a check of the rules a step list must meet.                         |

`dfo-spec-kit-frame-grammar` depends on `dfo-spec-kit`. `dfo-spec-kit-components` depends on `dfo-spec-kit-descriptions`. The other three have no dependency inside the kit.

## What's inside

```text
docs/         normative specifications: identity-v1.md, frame-grammar-v1.md, and maintainer notes
policies/     compatibility policy per artifact family
python/       the five packages
registries/   codes.json (warning and refusal codes) and units.json (unit refs)
schemas/      JSON Schema for the registries and the vector suites
tools/        validators for the registries and vector suites
vectors/      cross-language conformance vectors for identity and units
```

## Getting started

No package is published to PyPI. Install from git, pinned to one commit. Replace `<rev>` with a commit SHA from this repository.

```bash
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/spec-kit"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/frame-grammar"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/dfo-spec-kit-components"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/provenance-docs"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/descriptions"
```

A package declares its kit dependency by name only. uv resolves it from the same commit through a path source in the package's `[tool.uv.sources]`, so `uv add` of `frame-grammar` also installs `dfo-spec-kit`. Other installers read the bare name and fail, so with pip name both distributions at the same commit:

```bash
pip install \
  "dfo-spec-kit-components @ git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/dfo-spec-kit-components" \
  "dfo-spec-kit-descriptions @ git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/descriptions"
```

A uv project that imports more than one kit distribution pins each in its own `[tool.uv.sources]`, every entry at the same `rev`:

```toml
[tool.uv.sources]
dfo-spec-kit-components = { git = "https://github.com/DevelopmentFinanceObservatory/spec-kit.git", rev = "<rev>", subdirectory = "python/dfo-spec-kit-components" }
dfo-spec-kit-descriptions = { git = "https://github.com/DevelopmentFinanceObservatory/spec-kit.git", rev = "<rev>", subdirectory = "python/descriptions" }
```

The kit needs no access to ONE's Google Cloud project. Everything here installs, runs, and tests locally.

## Documentation

- [docs/identity-v1.md](docs/identity-v1.md) specifies what a spec identity's bindings denote.
- [docs/frame-grammar-v1.md](docs/frame-grammar-v1.md) specifies the frame byte grammar and `content_version`.
- Each package README under `python/` documents that package's API and vector suite.
- [policies/](policies/README.md) states which change to which artifact needs which version bump.
- [docs/development.md](docs/development.md) holds maintainer notes.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT. See [LICENSE](LICENSE).
