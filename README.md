# dfo-spec-kit

The executable form of the platform's shared contracts. It exists so the consumers of these contracts, the Search API in `apis`, the harness and Analysis MCP in `ai`, the website, and the docs, share one source of truth instead of hand-maintained mirrors. This repo is not the spec registry nor the op-spec schema.

The target design (repository shape, artifact families, versioning, governance, and delivery sequence) lives in the kit design, maintained outside this repo. This README only orients. That document is authoritative.

## Two schema families

The kit carries two schema families.

The **dataset-spec family** (spec models, identity projection, JCS canonicalisation, SHA-256 hashing) is Python-first. The behaviour lives in Python, and JSON Schema is exported from the pydantic models.

The **component-spec family** (the presentation catalogue, composition schema, operation union) is TypeScript-first. Zod v4 is the author and validator, and generated artifacts (TS prop types, Pydantic models, tool metadata) are frozen and checked for drift. Each family is authored in the language where its behaviour lives. Committed JSON Schema is the neutral frozen centre between them, held together by cross-language conformance fixtures.

## Status

Six Python distributions exist so far. `python/spec-kit/` (`dfo-spec-kit`, import `dfo_spec_kit`): `identity/` implements the spec1 identity scheme (role-slot projection, JCS canonicalisation, SHA-256 `spec_id` minting), conformance-tested against the vector suite. `units/` reads a unit ref into a typed value (`Money`, `UnpricedMoney`, `Percent`, `Count` or `Other`) from `registries/units.json`, conformance-tested against `vectors/units/`. Money whose ref leaves prices unstated cannot be compared until `complete_prices` applies the StatVar's measurement qualifier, and constant-price money with no base year cannot be compared at all. `models/` has no code yet; model extraction comes next. `python/frame-grammar/` (`dfo-spec-kit-frame-grammar`, import `dfo_frame_grammar`): the frame grammar's executable form -- canonical frame bytes and `content_version` minting for a materialised observation table -- conformance-tested against its own vector suite, and depending on `dfo-spec-kit` for the identity canonicalisation primitives it reuses. `python/dfo-spec-kit-components/` (`dfo-spec-kit-components`, import `dfo_spec_kit_components`): the presentation contract bundle as package data, holding the envelope schema, the per-member component schemas, the write-side intent grammar, and their conformance fixtures. Generated Pydantic models for the catalogue land in this same package once the catalogue freezes. `python/provenance-docs/` (`dfo-spec-kit-provenance-docs`, import `dfo_provenance_docs`): the provenance documentation grammar, which decodes the escaped Markdown documents a Provenance node carries and parses the transformations, changelog, methodology and notes templates, conformance-tested against its own vector suite. `python/descriptions/` (`dfo-spec-kit-descriptions`, import `dfo_descriptions`): the step records and descriptions contract, holding the step schema, the operation list, the description schema and a check of the structure rules a step list must meet beyond its schema, conformance-tested against its own vector suite. `python/client-contract/` (`dfo-spec-kit-client-contract`, import `dfo_client_contract`): the contract the DFO Python and TypeScript clients are built to, as package data: the served OpenAPI document of the composed dfo-apis app, the response members the clients act on, the caller's next step for each refusal code, the scenario and `DatasetRef` formats with their records, and the client rules with their vectors. dfo-apis CI checks its app against the same files.

The Python distributions live at `python/spec-kit/`, `python/frame-grammar/`, `python/dfo-spec-kit-components/`, `python/provenance-docs/`, `python/descriptions/`, and `python/client-contract/`, each its own installable unit with its own version and dependency surface. A TypeScript package (`ts/`) opens only when the component catalogue freeze lands.

`dfo-spec-kit-frame-grammar` depends on `dfo-spec-kit` as an intra-repo path source rather than a git dependency, so the two packages develop together in this repo: a change to identity canonicalisation and a change here that depends on it land in the same commit. `dfo-spec-kit-components` depends on `dfo-spec-kit-descriptions` as an intra-repo path source, because the envelope's `description` references `description.schema.json` by its `$id`. A consumer pins both distributions at one revision. Its catalogue bundle is static data plus one accessor function, `bundle_dir()`. `dfo-spec-kit-provenance-docs` also stands alone, depending on `markdown-it-py` only. `dfo-spec-kit-descriptions` depends on `rfc8785` only. `dfo-spec-kit-client-contract` depends on the standard library only.

Publishing is undecided: no registry workflow exists for any package. Until that's resolved, install a Python package as a git dependency pinned to one commit, e.g.

```bash
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/spec-kit"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/frame-grammar"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/dfo-spec-kit-components"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/provenance-docs"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/descriptions"
uv add "git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/client-contract"
```

A package's metadata names its intra-repo dependency by distribution name alone, and no index serves these distributions. uv reads the path source in `[tool.uv.sources]` and resolves the dependency from the same checkout, so `uv add` of `frame-grammar` or `dfo-spec-kit-components` alone installs `dfo-spec-kit` or `dfo-spec-kit-descriptions` at that commit. Every other installer reads the bare name and fails, so name both distributions at the same commit:

```bash
pip install \
  "dfo-spec-kit-components @ git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/dfo-spec-kit-components" \
  "dfo-spec-kit-descriptions @ git+https://github.com/DevelopmentFinanceObservatory/spec-kit@<rev>#subdirectory=python/descriptions"
```

A uv project pins each kit distribution it imports in its own `[tool.uv.sources]`, every entry at the same `rev`, as `apis` pins `dfo-spec-kit-components` and `dfo-spec-kit-frame-grammar`:

```toml
[tool.uv.sources]
dfo-spec-kit-components = { git = "https://github.com/DevelopmentFinanceObservatory/spec-kit.git", rev = "<rev>", subdirectory = "python/dfo-spec-kit-components" }
dfo-spec-kit-descriptions = { git = "https://github.com/DevelopmentFinanceObservatory/spec-kit.git", rev = "<rev>", subdirectory = "python/descriptions" }
```

## Layout

```
dfo-spec-kit/
├── docs/frame-grammar-v1.md          # the normative frame grammar / content_version specification
├── docs/identity-v1.md               # the normative identity binding-state denotation specification
├── schemas/registry/                 # JSON Schema for the code registry
├── schemas/spec/                     # frozen JSON Schema exported from the pydantic models
├── schemas/vectors/                  # record schemas + docs for the identity and unit vector suites
├── registries/codes.json             # the seeded warning/refusal code registry
├── registries/units.json             # the unit refs dfo_spec_kit.units types, its currency prefixes and codes
├── vectors/identity/                 # cross-language identity conformance fixtures
├── vectors/units/                    # cross-language typed-unit conformance fixtures
├── python/spec-kit/                  # the dfo-spec-kit package (import dfo_spec_kit)
├── python/frame-grammar/             # the dfo-spec-kit-frame-grammar package (import dfo_frame_grammar),
│                                      # its own frame grammar vector suite shipped as package data
├── python/dfo-spec-kit-components/   # the dfo-spec-kit-components package (import dfo_spec_kit_components),
│                                      # the presentation catalogue bundle shipped as package data
├── python/provenance-docs/           # the dfo-spec-kit-provenance-docs package (import dfo_provenance_docs),
│                                      # the provenance documentation grammar and its vector suite as package data
├── python/descriptions/              # the dfo-spec-kit-descriptions package (import dfo_descriptions),
│                                      # the step and description schemas, the operation list and their vector suite as package data
├── python/client-contract/           # the dfo-spec-kit-client-contract package (import dfo_client_contract),
│                                      # the served OpenAPI document, strict fields, caller actions, scenarios, ref format and client rules as package data
├── policies/                         # compatibility policy per artifact family
└── tools/                            # codegen + drift-check scripts, including the registry and vector validators
```
