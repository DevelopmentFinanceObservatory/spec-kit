# Maintainer notes

Background on how the repository fits together. Contributors do not need it to open a pull request (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Two schema families

The kit carries two schema families, each authored in the language where its behaviour lives. Committed JSON Schema is the neutral centre between them, held together by cross-language conformance vectors.

The dataset-spec family (spec models, identity projection, JCS canonicalisation, SHA-256 hashing) is Python-first. The behaviour lives in `python/spec-kit`, and `schemas/spec/` is the place for JSON Schema exported from its pydantic models. It holds only a README today, because `dfo_spec_kit.models` has no code yet.

The component-spec family (the presentation catalogue, composition schema, operation union) is TypeScript-first: Zod v4 authors and validates, and generated artifacts (TypeScript prop types, Pydantic models, tool metadata) are checked for drift. The family's artifact in this repository is the catalogue bundle in `python/dfo-spec-kit-components/`. The repository has no TypeScript package.

[policies/dataset-spec.toml](../policies/dataset-spec.toml) and [policies/component-spec.toml](../policies/component-spec.toml) state the compatibility rules for each family.

## Dependencies between packages

`dfo-spec-kit-frame-grammar` depends on `dfo-spec-kit` through a path source (`../spec-kit`, editable) in its `[tool.uv.sources]`. The two packages develop together here, so a change to identity canonicalisation and a change in the frame grammar that depends on it land in the same commit.

`dfo-spec-kit-components` depends on `dfo-spec-kit-descriptions` the same way, because the envelope's `description` references `description.schema.json` by its `$id`. The catalogue bundle is static data plus one accessor function, `bundle_dir()`.

`dfo-spec-kit-provenance-docs` depends on `markdown-it-py` only. `dfo-spec-kit-descriptions` depends on `rfc8785` only.

A package's published metadata names its kit dependency by distribution name alone, and no index serves these distributions. uv reads the path source and resolves the dependency from the same checkout. Every other installer reads the bare name and fails, which is why the README shows pip installs that name both distributions at one commit. A consumer pins both at one revision.

## Releases

No registry workflow exists for any package, so releases are git revisions. The compatibility policies define "released" as reachable from the most recent git tag matching `v*`. `tools/validate_registry.py` diffs the working registry against that tag to enforce that published code entries do not change, apart from the deletion exception named in the script and growth of a published entry's `mappings`. This repository has no tag reachable from `dev`, so the check is skipped and the `registry` CI job fetches full history so that it can run once one exists.

## Ownership

`CODEOWNERS` lists a maintainer for `/schemas/`, `/registries/`, `/vectors/`, and `/python/`.
