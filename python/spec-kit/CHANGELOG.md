# Changelog

All notable changes to the `dfo-spec-kit` Python package will be documented in
this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `dfo_spec_kit.units` reads a unit ref into one typed value: `Money`
  (`currency`, `prices`, `base_year`, `chained`), `UnpricedMoney`
  (`currency`), `Percent`, `Count` or `Other` (`ref`). A ref that leaves
  prices unstated (`Currency<ISO>`, a bare code, `USDollar` and the other
  named twins, the UN `CR_USD` and `CR_EUR`) parses to `UnpricedMoney`, which
  raises `TypeError` on any comparison and is unhashable. `complete_prices`
  gives it the price basis of the StatVar's measurement qualifier
  (`Nominal` current, `RealValue` constant) as a comparable `Money`, so
  `CurrencyUSD`, `USDollar` and `USD` are one value once completed under one
  qualifier. A constant-price `Money` with no base year raises the same
  `TypeError` on comparison and hashing, and `parse_unit` returns a new
  object on every call, so neither incomparable value matches by identity
  in a set, dict or list.
  Chained-dollar refs carry `chained=True`, so they never equal a
  fixed-base amount of the same base year. `Money` refuses with
  `ValueError` a `base_year` that is not an `int` (a `bool` included), a
  `prices` that is not a string, and a `chained` that is not a `bool` or is
  true for current prices. A bare ref is money only when it is an ISO 4217
  code from the registry's `currency_codes`, so `TEU` parses as `Other`. A
  ref that scales its values (`SDG_CU_USD_M`, `SDG_NUM_TH`,
  `PeopleInThousands`) parses as `Other`.
- `dfo_spec_kit.currency` holds `is_currency_code`, the currency shape test
  the identity and the frame grammar share. It reads no data file, so
  importing `dfo_spec_kit.identity` never loads the unit registry.
- `registries/units.json` declares 29 typed refs and 176 currency codes, with
  a byte-identical package mirror. `schemas/registry/unit-registry.schema.json`
  closes each row to its kind's members and `tools/validate_unit_registry.py`
  checks it, the sort order, uniqueness, the prefix rule and the mirror in
  CI; the loader refuses a row whose members do not match its kind.
  `vectors/units/` holds 40 conformance vectors validated by
  `schemas/vectors/unit-vector.schema.json` and
  `tools/validate_unit_vectors.py`, which CI runs.
- The identity vector `unit-request-recipe-rejected` pins the identity's
  unit request at `currency`, `prices` and `base_year`: a `recipe` member,
  which a converted frame cell may carry, refuses with `unknown_member`.
- `source_code_not_held`, `source_code_ambiguous` and
  `source_code_shares_variable` are `search.resolve` refusals on the
  `no_data_reason` channel in `registries/codes.json` and its bundled mirror.
  A part's `source_code` whose series no served source can answer the part
  with raises the first: no served source holds the code, the part's
  `sources` exclude every holder, or the code's series cannot answer the
  part as asked. Its detail names the cause and the sources whose codes
  Search holds. One held by more than one
  source, with none named in `sources`, raises the second, naming each. One
  that shares its StatVar with other codes of the same source, where the graph
  does not record what separates them, raises the third, naming the codes
  that share the variable. The retired `code_ambiguous` is unchanged.

### Changed

- On `presentation.write`, codes `binding_invalid`, `constraint_violation`
  and `resource_exhausted` map at 400, `frame_unavailable` at 404 and
  `release_mismatch` at 503, the statuses the Presentation write routes
  serve. `presentation.render` keeps 422 for the first two and 409 for the
  rest. `frame_unavailable` covers a frame that is not held in a readable
  state. A frame store that fails to answer is `service_unavailable`.
- Code `edit_conflict` maps to `presentation.render` at 409, for a render
  of a component that has no current revision, and its definition names
  that case.
- Codes `identity_quarantined` (500), `service_unavailable` (503) and
  `internal_error` (500) map to `datasets.description` at the statuses they
  have on `datasets.snapshot`.
- Code `node_not_held` maps to `graph.members` at 404, for a node the
  deployment's graph does not hold.
- Code `upstream_error` maps to `graph.members` at 502, the status the
  members route answers when the read that checks whether the graph holds
  the node fails upstream, as `graph.node` answers for the same read.
- `starved_axis_binding_restored` is retired in `registries/codes.json` and
  its bundled mirror: Search no longer restores a starved constraint axis, so
  nothing raises it.
- Code `query_qualifier_dropped` maps to the `parsed` door of `search.resolve`
  as well as `raw_text`, since the resolver also drops a qualifier it cannot
  apply on parsed requests, such as a source exclusion that removed nothing.
- Code `facet_binding_unsupported` maps to `datasets.mint`,
  `datasets.definition` and `datasets.options` at 422, the status those doors
  send. Its `search.resolve` and `search.options` mappings stay at 501.
- Existing codes map onto the Datasets doors that raise them:
  `snapshot_revoked` (410) on `datasets.definition` and `datasets.options`;
  `spec_id_mismatch` (422), `rate_coverage_gap` (422) and
  `capability_unavailable` (501) on `datasets.options`;
  `service_unavailable` (503) on `datasets.operations`; `rollup_not_summable`
  (422) on `datasets.definition`; and `identity_collision` (500) on
  `datasets.definition`, `datasets.options` and `datasets.members`.
- `datasets.refresh` carries the refusals `datasets.definition` raises, at
  the same statuses: `snapshot_revoked` (410), `timeout` (504),
  `upstream_error` (502), `upstream_version_changed` (503),
  `identity_collision` (500), `members_not_held` (500), `spec_not_exportable`
  (409), `capability_unavailable` (501), and at 422
  `collection_probe_ceiling`, `default_facet_undefined`,
  `definition_not_reproducible`, `excluded_by_registry`,
  `facet_not_available`, `member_not_resolvable`, `over_ceiling`,
  `parquet_size_bound`, `role_signature_incompatible`, `rollup_not_summable`,
  `rate_coverage_gap`, `recipe_not_declared` and `facet_binding_unsupported`.
  `snapshot_revoked` (410) also maps to `datasets.mint`.
- Code `identity_quarantined` (500) maps to `datasets.snapshot`,
  `datasets.files`, `datasets.pin`, `datasets.observations` and
  `datasets.refresh`, the same status it has on the other Datasets doors.

### Added

- A unit facet `value` binding may carry `"national_currency": {"role": R}`,
  the ask for the facets whose unit is each `R` entity's own currency, served
  as published. It mints its own id, apart from the conversion request naming
  the same role. A value binding carries exactly one of `ref`, `request` and
  `national_currency`; the member on another axis, beside `ref` or `request`,
  or in a `set` refuses `invalid_binding_shape`, an unknown role refuses
  `schema_violation`, and a member beside `role`, such as `prices`, refuses
  `unknown_member`. `docs/identity-v1.md` states the denotation.
  `IDENTITY_VERSION` stays `"spec1"`, and no existing id moves, since the
  member refused before.
- Graph endpoints `graph.nodes`, `graph.members`, `graph.roots`,
  `graph.documentation` and `graph.sources`, and the Datasets endpoint
  `datasets.description`, in `schemas/registry/code-registry.schema.json`.
  Existing codes map onto them at the statuses the doors send:
  `request_invalid` (400) on `graph.nodes`, `graph.members`,
  `graph.documentation` and `datasets.description`;
  `unsupported_schema_version` (422) on `graph.nodes`; `service_unavailable`
  (503) on `graph.nodes`, `graph.members`, `graph.roots` and
  `graph.documentation`; `upstream_error` (502) on `graph.nodes` and
  `graph.documentation`; `node_not_held` (404) on `graph.documentation`; and
  `snapshot_not_found` (404), `snapshot_revoked` (410) and `spec_not_found`
  (404) on `datasets.description`. `graph.sources` carries no permanent
  codes.
- Code `slot_default_not_applied` in `registries/codes.json` and its bundled
  mirror: a `search.resolve` warning on the raw_text, parsed and
  dataset_resubmit doors. It flags a shape slot default, declared per measure
  class, that is skipped because no member holding the default value has data
  at the place while another member does, so the part answers on the slot's
  other members.
- Endpoint `datasets.files` in `schemas/registry/code-registry.schema.json`:
  `GET /api/datasets/{id}/{content_version}/files/{digest}`, which answers a
  stored artifact with a 302 redirect to its download link. It carries
  `snapshot_not_found` (404), `snapshot_revoked` (410), `artifact_not_found`
  (404), `artifact_expired` (410), `service_unavailable` (503) and
  `spec_not_found` (404).
- Codes `source_not_resolved` and `source_ambiguous` in `registries/codes.json`
  and its bundled mirror: refusals on `search.resolve`, on the `no_data_reason`
  channel, for a source mention whose text matched no admitted source, dataset
  or provenance, and for one that matched two or more unrelated sources, whose
  detail lists the candidates.
- Codes `denominator_unverified`, `artifact_pair_stale` and
  `catalogue_index_drift` in `registries/codes.json` and its bundled mirror:
  `search.resolve` warnings on the raw_text, parsed and dataset_resubmit doors.
  `denominator_unverified` flags a named denominator whose series declares no
  denominator arc, so the ratio base is read from the series name only.
  `artifact_pair_stale` flags an origin catalogue build and a series coverage
  build from different graph ingestions, so the answer may lack observations
  ingested after the older build. `catalogue_index_drift` flags a variable
  whose catalogue schema disagrees with the packaged code index on five-tuple,
  constraint properties, or roles; the answer uses the catalogue's reading.
- Code `members_absent_from_graph` in `registries/codes.json` and its bundled
  mirror: a `search.resolve` warning on the dataset_resubmit door naming the
  elected members the knowledge graph holds no node for, which the answer
  leaves out.
- Endpoints `datasets.refresh` (`POST /api/datasets/frames/refresh`),
  `datasets.recipe` (`PUT` and `GET /api/datasets/frames/{spec_id}/{content_version}/recipe`, and `GET /api/datasets/recipes/replays/{replay_key}`) and
  `presentation.render` (`POST /api/presentation/components/{component_id}/revisions/{revision}/render`
  and `GET .../render/data`) in the registry vocabulary
  (`schemas/registry/code-registry.schema.json` and its README).
- Codes in `registries/codes.json` and its bundled mirror, all refusals on
  the `problem_details` channel: `frame_not_refreshable` (422, a derived
  frame named to `datasets.refresh`), `rendering_not_recorded` (422, a
  grouped rendering whose grouping no record states, on `datasets.refresh`),
  `refresh_not_permitted` (403, a caller that is not the live-refresh
  principal, on `datasets.refresh` and `presentation.render`),
  `render_target_mismatch` (409, a target frame that is not a later state of
  the stored revision's frame, on `presentation.render`) and
  `recipe_not_found` (404, no recipe recorded for the named frame, or no
  frame stored under the named replay key, on `datasets.recipe`).
- Mappings of existing codes onto the new endpoints, at the statuses they
  already carry. Onto `datasets.refresh`: `snapshot_not_found` (404),
  `spec_not_found` (404), `request_invalid` (400), `service_unavailable`
  (503) and `internal_error` (500). Onto `datasets.recipe`:
  `publish_conflict` (409, a recipe that differs from the one held for the
  pair, or a replay key another frame holds), `publish_not_permitted` (403),
  `snapshot_not_found` (404, a recipe write for a pair with no stored
  version), `request_invalid` (400), `service_unavailable` (503) and
  `internal_error` (500). Onto `presentation.render`: `not_found` (404),
  `request_invalid` (400), `schema_invalid` (400), `binding_invalid` (422),
  `constraint_violation` (422), `resource_exhausted` (409),
  `frame_unavailable` (409), `release_mismatch` (409), `internal_error`
  (500), `service_unavailable` (503) and `timeout` (504).
- Endpoint `datasets.members` (`POST /api/datasets/{id}/members`) in the
  registry vocabulary (`schemas/registry/code-registry.schema.json` and its
  README). `request_invalid` (400), `spec_not_found` (404), `spec_id_mismatch`
  (422), `member_not_resolvable` (422), `spec_not_exportable` (409),
  `role_signature_incompatible` (422), `definition_not_reproducible` (422),
  `facet_not_available` (422), `over_ceiling` (422), `service_unavailable`
  (503), `upstream_error` (502), `timeout` (504), `identity_quarantined` (500)
  and `internal_error` (500) map onto it in `registries/codes.json` and its
  bundled mirror, on the `problem_details` channel.
- Code `open_role_unavailable` in `registries/codes.json` and its bundled
  mirror: a 422 refusal on `search.resolve` for a `dataset_resubmit` whose
  identity names an open role, when the StatVars the definition elects declare
  no role on that axis, so the definition cannot give one series per member.
- Optional top-level identity member `open_roles` in
  `dfo_spec_kit.identity`: a list of flow roles, from `origin` and
  `destination`, whose every member the request asks for, as distinct from an
  absent role, which asks for the total. The projection sorts and
  de-duplicates it and leaves it out when empty or absent, so no existing
  `spec_id` moves. A role also bound in `entities`, a role outside the set, a
  non-array value, and a non-empty `open_roles` on a `stat_var` shape
  refuse as `schema_violation`. An empty list is dropped. Nine `open-roles-*`
  vectors in `vectors/identity/` pin it, and `docs/identity-v1.md` moves to
  `1.2.0` with its denotation.
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

- `base_not_found`, `base_identity_mismatch` and `edit_incomplete` are retired
  in `registries/codes.json` and its bundled mirror: a `dataset_resubmit`
  identity entry carries the whole definition, so the resubmit no longer
  reconstructs a base from `base_spec_id` or diffs an edit against it, and
  nothing raises them.
- `multi_recipient_truncated`, `child_places_clamped` and
  `standard_probe_places_clamped` are retired in `registries/codes.json` and
  its bundled mirror: Search binds every place a request names, up to one
  bound, and refuses a larger set with `entity_set_too_large`, so no list of
  places is truncated or clamped and nothing raises them.
- `skill_content_absent`, `skill_content_too_large`, `skill_content_undecodable`
  and `skills_corpus_budget_exhausted` are `skills.list` warnings for a skill
  left out of the catalogue, and no longer map onto `skills.fetch`.
- `code_ambiguous` and `members_deduplicated` are retired in
  `registries/codes.json` and its bundled mirror: Datasets no longer serves
  collections, source-code lookups or multi-StatVar mints, so nothing raises
  them. `code_not_found` loses its `datasets.mint` mapping and stays the
  platform `GET /codes/{code}` miss. `spec_not_found` and `spec_id_mismatch`
  no longer mention collections or member lists, and
  `collection_probe_ceiling` describes a request group's facet probe.
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
