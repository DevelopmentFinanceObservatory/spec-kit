# Changelog

All notable changes to the `dfo-spec-kit` Python package will be documented in
this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Code `deflator_missing_omitted` in `registries/codes.json` and its bundled
  mirror: a warning on the Datasets mint, definition, options and snapshot
  manifests and on `search.resolve` diagnostics for a constant-price
  conversion that left out an entity's rows in the years the deflator table
  holds no value for it, or all its rows when the table holds no value for it
  in the base year, and names those entities and years.
- Mappings of `observations_preview_too_large`,
  `observations_windowed_scope_unsupported` and
  `observations_converted_scope_unsupported` on `datasets.observations` in
  `registries/codes.json` and its bundled mirror, on the
  `observations_omission_reason` channel. The Datasets observations body
  carries the same `omitted` object as a `search.resolve` Spec, and the channel
  description in `schemas/registry/code-registry.schema.json` says so. The
  windowed-scope and converted-scope codes also gain the `search.resolve`
  mapping on that channel, beside their `diagnostics_warning` one, and the
  schema README names the three warning-classified omission reasons.
- Code `observations_unavailable` in `registries/codes.json` and its bundled
  mirror, a 404 refusal on the new `datasets.observations` endpoint
  (`GET /api/datasets/{id}/{content_version}/observations`) for a version the
  observations read cannot build from. Such a version has no stored frame, a
  converted series without its per-series conversion record, no stored default
  place scope, or is a collapsed version. The same endpoint carries
  `artifact_expired`, `internal_error`, `service_unavailable`,
  `snapshot_not_found`, `snapshot_revoked` and `spec_not_found` with the
  statuses they have on `datasets.snapshot`.
- Code `unit_conversion_unavailable` in `registries/codes.json` and its bundled
  mirror: a `search.resolve` diagnostics warning for an answer in a currency
  served while the API's conversion catalogue is not loaded, so the answer
  states no `unit_conversion` offer although its data may convert.
- Code `currency_unmapped_omitted` in `registries/codes.json` and its bundled
  mirror: a warning on the Datasets mint, definition, options and snapshot
  manifests and on `search.resolve` diagnostics for a conversion into each
  entity's own currency that left out the rows of entities no single currency
  names through `nationalCurrencyOf`, and names those entities.
- Code `membership_unverified` in `registries/codes.json` and its bundled
  mirror: a `search.resolve` warning on the raw_text, parsed and
  dataset_resubmit doors for a role-bearing answer whose members the mint's
  member walk did not settle, which the response cache does not store.
- Code `family_total_absent` in `registries/codes.json` and its bundled mirror:
  a `search.resolve` warning on the raw_text, parsed and dataset_resubmit doors
  for an answer whose family total has no data at the place, so it shows the
  closest family member by rollup instead.
- A facet `value` binding on the `measurementMethod` or `unit` axis may carry
  `"ref": null`, the ask for facets that declare no value on that axis. It
  mints its own id, apart from an unbound axis. A null ref on `provenance`,
  as a facet `set` member, or on a slot refuses `schema_violation`.
  `docs/identity-v1.md` states the denotation. `IDENTITY_VERSION` stays
  `"spec1"`, and no existing id moves, since a null ref refused before.
- Code `slot_defaulted` in `registries/codes.json` and its bundled mirror: a
  `search.resolve` warning on the raw_text, parsed and dataset_resubmit doors
  for a query that named a slot by its plain name, such as flow, with no value,
  where a declared default fixed the slot to one value.
- Code `skill_frontmatter_invalid` in `registries/codes.json` and its bundled
  mirror: a `skills.list` warning for a skill left out of the catalogue because
  its front matter is missing, unparsable, lacks a string `name` or
  `description`, or names a different id.
- Codes `withheld_by_registry`, `un_system_unavailable`,
  `origin_catalogue_stale` and `excluded_by_registry` in
  `registries/codes.json` and its bundled mirror. The first is a warning for a
  series left empty because every facet sits under a provenance the origin
  registry does not admit. The second is a warning for a UN System Data
  Commons that did not answer for the resolve or for reads at named places; it
  also reaches the datasets manifest warnings. The third is a warning for an
  origin catalogue more than 14 days old. The fourth is a `datasets.mint`,
  `datasets.definition` and `datasets.options` refusal (422) for a variable
  whose data at the requested places sits only under provenances the registry
  does not admit.
- `dfo_spec_kit.registry`: typed access to the platform code registry, loaded
  from a mirror of `registries/codes.json` bundled as package data. The
  repo-root file stays canonical; `tools/validate_registry.py` fails if the
  two diverge.
- `dfo_spec_kit.identity`: `IDENTITY_VERSION` is now part of the public API,
  so a consumer can import the spec1 literal instead of spelling it.
- `EXPORTS.md`: the export manifest defining this distribution's
  compatibility boundary, governed by `policies/dataset-spec.toml`.
- A slot binding gains a fourth state, `exact`: `{"state": "exact", "ref": "<dcid>"}`, the deliberate parent-only ask on a hierarchical code, denoting
  the bound code alone rather than the code plus its subtree `value` denotes.
  Carrying `refs` on an `exact` binding refuses `invalid_binding_shape`; a
  missing `ref` refuses `schema_violation`; `exact` on a facet binding
  refuses `schema_violation`, since `exact` is a slot-binding-only state.
  `docs/identity-v1.md` is the new normative home for what each binding
  state denotes.

### Changed

- `skill_content_absent`, `skill_content_too_large`, `skill_content_undecodable`
  and `skills_corpus_budget_exhausted` are `skills.list` warnings for a skill
  left out of the catalogue, and no longer map onto `skills.fetch`.
- Breaking. The normative spec1 identity vectors and worked example now use
  `origin` and `destination` flow roles (with `observationAbout` retained for
  the observed subject) rather than `provider` and `recipient`. The identity
  core remains role-vocabulary agnostic. `IDENTITY_VERSION` remains `"spec1"`;
  every affected `spec_id` must be re-minted.
- Breaking. The identity core's opaque `shape_id` string becomes `shape`, a
  closed tagged union on a required `kind`. The described arm
  (`kind: "statvar_schema"`) carries up to five sparse members:
  `population_type`, `measured_property`, `stat_type`,
  `measurement_qualifier`, and a `measurement_denominator` sub-union of
  either `{"ref": "<dcid>"}` or `{"unresolved": true}`. An arm carrying none
  of the five refuses. The pinned arm (`kind: "stat_var"`) carries one
  required dcid `id` and requires an empty sibling `slots` array. A document
  carrying `shape_id` refuses as an unknown member.
- `IDENTITY_VERSION` stays `"spec1"`. The scheme is redefined in place,
  ahead of any minted `spec_id` entering service. Every id computed under
  the previous reading of spec1 must be re-minted.
- Every bare dcid inside `shape` is checked against the identity core's dcid
  grammar: after NFC, non-empty, within the 1,024 code-point cap, free of
  Unicode whitespace and of Cc/Cf characters, and carrying no `:`. Identity
  carries bare dcids, so a `dcid:`-prefixed spelling refuses
  `schema_violation`.
- Breaking. The dcid grammar above now also runs on every slot ref: a
  `value` binding's `ref`, each member of a `set` binding's `refs`, and an
  `exact` binding's `ref`. A `dcid:`-prefixed slot ref, previously accepted,
  now refuses `schema_violation`, ahead of every reader rather than left to
  each consumer to guard.
- A slot binding in the `unbound` state is retained in the projection: the
  wire document and the projection carry the same slot count, every
  shape-declared property appearing at minimum as `{"state": "unbound"}`.
  Reprojecting an already-canonical document, unbound slots included, leaves
  it unchanged.
- Two slots sharing a property refuse `schema_violation`, whatever their
  binding states: the sort by property is not a total order, so an
  unrefused duplicate would let input order pick a winner and mint different
  ids for two orderings of the same slot list. (`duplicate_keys` stays
  reserved for an object repeating a key; slot entries are array members.)
- The top-level `dfo_spec_kit` package no longer re-exports names from its
  subpackages. Each name is public from one path only: import
  `IDENTITY_VERSION` from `dfo_spec_kit.identity` and `REGISTRY` from
  `dfo_spec_kit.registry`.

## [0.1.0]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- `dfo_spec_kit.identity`: the spec1 identity implementation (strict I-JSON
  parse, schema validation, normalisation, JCS canonicalisation, SHA-256
  `spec_id` minting), conformance-tested against the identity vector suite.

### Fixed

- `dfo_spec_kit.identity`: a unit conversion request's currency code is now
  matched against the full string, not just its start, so a trailing newline
  after an otherwise valid three-letter code is rejected as
  `invalid_unit_request` rather than passing validation. This deliberately
  corrects the reference implementation this package was extracted from.

[unreleased]: https://github.com/ONEcampaign/dfo-spec-kit/commits/main/python
