# dfo-spec-kit

The executable form of the platform's shared contracts. It exists so the consumers of these contracts, the Search API in `dfo-apis`, the harness and Analysis MCP in `dfo-ai`, the website, and the docs, share one source of truth instead of hand-maintained mirrors. This repo is not the spec registry nor the op-spec schema.

The target design (repository shape, artifact families, versioning, governance, and delivery sequence) lives in the kit design, maintained outside this repo. This README only orients. That document is authoritative.

## Two schema families

The kit carries two schema families.

The **dataset-spec family** (spec models, identity projection, JCS canonicalisation, SHA-256 hashing) is Python-first. The behaviour lives in Python, and JSON Schema is exported from the pydantic models.

The **component-spec family** (the presentation catalogue, composition schema, operation union) is TypeScript-first. Zod v4 is the author and validator, and generated artifacts (TS prop types, Pydantic models, tool metadata) are frozen and checked for drift. Each family is authored in the language where its behaviour lives. Committed JSON Schema is the neutral frozen centre between them, held together by cross-language conformance fixtures.

## Status

Four Python distributions exist so far. `python/spec-kit/` (`dfo-spec-kit`, import `dfo_spec_kit`): `identity/` implements the spec1 identity scheme (role-slot projection, JCS canonicalisation, SHA-256 `spec_id` minting), conformance-tested against the vector suite. `models/` has no code yet; model extraction comes next. `python/frame-grammar/` (`dfo-spec-kit-frame-grammar`, import `dfo_frame_grammar`): the frame grammar's executable form -- canonical frame bytes and `content_version` minting for a materialised observation table -- conformance-tested against its own vector suite, and depending on `dfo-spec-kit` for the identity canonicalisation primitives it reuses. `python/dfo-spec-kit-components/` (`dfo-spec-kit-components`, import `dfo_spec_kit_components`): the presentation contract bundle as package data, holding the envelope schema, the per-member component schemas, the write-side intent grammar, and their conformance fixtures. Generated Pydantic models for the catalogue land in this same package once the catalogue freezes. `python/provenance-docs/` (`dfo-spec-kit-provenance-docs`, import `dfo_provenance_docs`): the provenance documentation grammar, which decodes the escaped Markdown documents a Provenance node carries and parses the transformations, changelog, methodology and notes templates, conformance-tested against its own vector suite.

The Python distributions live at `python/spec-kit/`, `python/frame-grammar/`, `python/dfo-spec-kit-components/`, and `python/provenance-docs/`, each its own installable unit with its own version and dependency surface. A TypeScript package (`ts/`) opens only when the component catalogue freeze lands.

`dfo-spec-kit-frame-grammar` depends on `dfo-spec-kit` as an intra-repo path source rather than a git dependency, so the two packages develop together in this repo: a change to identity canonicalisation and a change here that depends on it land in the same commit. `dfo-spec-kit-components` stands alone. Its bundle is static data plus one accessor function. `dfo-spec-kit-provenance-docs` also stands alone, depending on `markdown-it-py` only.

Publishing is undecided: no registry workflow exists for any package. Until that's resolved, install a Python package as a git dependency, e.g.

```bash
uv add "git+https://github.com/ONEcampaign/dfo-spec-kit#subdirectory=python/spec-kit"
uv add "git+https://github.com/ONEcampaign/dfo-spec-kit#subdirectory=python/frame-grammar"
uv add "git+https://github.com/ONEcampaign/dfo-spec-kit#subdirectory=python/dfo-spec-kit-components"
uv add "git+https://github.com/ONEcampaign/dfo-spec-kit#subdirectory=python/provenance-docs"
```

## Layout

```
dfo-spec-kit/
├── docs/frame-grammar-v1.md          # the normative frame grammar / content_version specification
├── docs/identity-v1.md               # the normative identity binding-state denotation specification
├── schemas/registry/                 # JSON Schema for the code registry
├── schemas/spec/                     # frozen JSON Schema exported from the pydantic models
├── schemas/vectors/                  # record schema + docs for the identity vector suite
├── registries/codes.json             # the seeded warning/refusal code registry
├── vectors/identity/                 # cross-language identity conformance fixtures
├── python/spec-kit/                  # the dfo-spec-kit package (import dfo_spec_kit)
├── python/frame-grammar/             # the dfo-spec-kit-frame-grammar package (import dfo_frame_grammar),
│                                      # its own frame grammar vector suite shipped as package data
├── python/dfo-spec-kit-components/   # the dfo-spec-kit-components package (import dfo_spec_kit_components),
│                                      # the presentation catalogue bundle shipped as package data
├── python/provenance-docs/           # the dfo-spec-kit-provenance-docs package (import dfo_provenance_docs),
│                                      # the provenance documentation grammar and its vector suite as package data
├── policies/                         # compatibility policy per artifact family
└── tools/                            # codegen + drift-check scripts, including the registry and vector validators
```
