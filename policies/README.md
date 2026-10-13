# policies

A policy file declares the compatibility contract for one artifact family
this repo publishes: what change to which artifact requires which version
bump, and what never changes once a release exists. One file per family,
named after it.

`dataset-spec.toml` governs the dataset-spec family (`python/spec-kit`'s
exports, `schemas/spec/`, `vectors/identity/`, and `registries/codes.json`).
`component-spec.toml` governs the component-spec family's first landed
artifact, the presentation catalogue bundle in
`python/dfo-spec-kit-components/`. It also governs the step and description
schemas and the operation list in `python/descriptions/`, since the
envelope references the description schema by `$id`. The TypeScript-first composition
schema and operation union join it when their artifacts exist to govern.

Each policy file is TOML for the same reason `python/spec-kit/EXPORTS.md`'s
manifest blocks are: it parses with the standard library alone, it carries
comments so the reasoning for a rule sits next to the rule, and it diffs
cleanly per field when a policy itself changes.
