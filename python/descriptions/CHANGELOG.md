# Changelog

All notable changes to the `dfo-spec-kit-descriptions` Python package will be
documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- `steps.schema.json`, the step list: one step per operation, with inputs by
  role, its fixed policy, the caller's parameters, what it kept, the data gaps
  it left out, its coverage and the unit it produces. A unit in constant
  prices names its `base_year`.
- A unit names its dcid as `ref`, its ISO 4217 code as `currency`, or both,
  so a converted unit is `{currency, prices, base_year}`. A side of a composed
  unit may be a variable, `{stat_var, label?}`, named by its bare dcid; a
  `dcid:` prefix is refused. A served description requires a variable side's
  `label`.
- `operations.json` and its schema: retrieval, the nine analysis operations,
  a step of the source's pipeline, the four choices made at mint and a
  card's selection, each with its input roles, policy, unit rule, coverage and
  known `left_out` reasons.
- `description.schema.json`, the description: `describes`, `title`, the five
  blocks, `caveats` in four closed kinds, each with `text`, the composer's
  sentence for it, `gaps`, `built_from`, and
  `rendering` with `frame` for a card rendered live. Each served step carries
  `text`, the composer's sentence for it, which a stored step list refuses:
  both schemas build a step from `step_members`, closed by
  `unevaluatedProperties`. A nullable member that is `null` with a gap naming
  it is unknown, and `null` with no gap means the data declares none.
- Each `describes.kind` requires the members its blocks always carry, each
  `null` with a gap when unknown, and refuses another kind's members. A
  dataset version requires `about.measure`, `fixed`, `varies`, `variables`,
  `series`, `period` and `unit`, all nine `freshness` members, and `cite.id`,
  `content_version`, `retrieved` and `data`. A computed version requires
  `about.name` (nullable), `series`, `period` and `unit`, `freshness.computed`,
  `inputs`, `latest_period`, `series_reaching_it` and both status counts,
  and `cite.inputs` beside its identity. A card revision requires its id,
  revision and version identity in `cite`; a card drawn from a version
  requires `about.drawn` and `unit` and its latest period and status counts,
  and a source card requires `about.name` and the download and release
  members.
- An open dimension with a known `count` requires `top`, `more` and its
  `values` link.
- A source that is not an input requires `publisher`, `licence` and
  `series`. A dataset version has no inputs, so its source refuses
  `content_version`; only a computed version and a card drawn from one carry
  inputs.
- `MAX_CITE_TEXT_LENGTH`, the bound on a description's `cite.text`, equal to
  the schema's `maxLength` for it.
- Description vectors for a source card, accepted, and for one that leaves out
  `freshness.next_release`, and a rejected dataset version whose source carries
  a `content_version`.
- `cite` and `frame` pair an id with its content version by family, `spec_`
  with `cv_` and `ops_` with `ocv_`.
- `join` lists the `unit_unbound` left-out reason, and `rank`'s unit rule is
  `same_as_input_or_none`: the input's unit, or none for an ordinal ranking.
- A computed version's input whose id is unknown, because no step retrieves
  it, is served with `id: null` in `freshness.inputs` and `cite.inputs` (the
  served `input_ref`), and in `sources` with `ref: null` and no `description`
  link, each with a gap. A source without `content_version` keeps a required
  string `ref`.
- A step whose operation writes a new variable (sum, quotient, share and
  compare, marked `new_variable` in `operations.json`) may name it in
  `stat_var`, by its bare dcid, so a reader names that variable from the
  step's output.
- A step's `sides` member: the entities each side of a two-sided operation
  holds, keyed by input role. A side maps each role column whose slice holds
  exactly one value to that entity, as a dcid in the form `params` use; a
  column with several values or none is absent, so an empty map is valid. A
  compare step records it, so a reader can say which entity each side holds,
  including when the sides are two separate retrievals that bind no entity.
- `check_steps`, which raises `StepsError` with a `StepsErrorReason` when a
  step list breaks a structure rule: the count and size bounds (`MAX_STEPS`,
  `MAX_STEPS_BYTES`), unique ids, inputs that name an earlier step, and a
  compare step without `sides` (`missing_sides`) or with `sides` keys other
  than its input roles (`sides_roles_mismatch`). The
  size bound counts the stored list as JCS (RFC 8785) UTF-8 JSON, which
  `steps_bytes` returns; a value JCS cannot serialise raises `not_i_json`.
  The package depends on `rfc8785`.
- `check_description`, which checks a description's steps against the count
  bound, I-JSON and the id, input and `sides` rules, and leaves the byte bound to the
  stored list, since served labels come from the vocabulary. It also raises
  `unknown_caveat_step` or `unknown_gap_step` when a caveat's `step`
  or a gap names a step its `steps` do not hold.
  It raises `drawn_values_count_mismatch` or `duplicate_drawn_value` when a
  drawn dimension's `values` differ in number from its `count` or repeat a `ref`.
- A gap inside `steps` names a step by id, `steps.<step id>.<member path>`, or
  is `steps.source_pipeline` or `steps.version`, a part of the list that is
  unknown; the schema refuses any other two-segment `steps.` path.
- Operation `change`, a stat card's change between its two basis periods, of
  kind `card_derivation`, with policy `change_between_basis_periods`, params
  `derivation` and `basis`, and unit rule `same_as_input_or_percent`.
- A source card's description fills `about.name` with the names of the sources
  it lists.
- The vector suite as package data, with its record schema.
- A computed version's `cite.data` is `/api/presentation/frames/{id}/{content_version}`,
  the route that serves a published frame's data; a `spec_` version's stays
  `/api/datasets/{id}/{content_version}`.
- An open dimension whose values nothing recorded serves `count: null` with a
  gap naming it, and carries no `top`, `more` or `values` link.
- A unit's `prices` is `current` or `constant`, and `base_year` is present
  exactly when prices are constant, as in the frame grammar and the identity
  core. A frame grammar test holds the three lists of prices equal.
- `version_family` in `steps.schema.json` pairs a version id with its content
  version, `spec_` with `cv_` and `ops_` with `ocv_`, for every version
  reference in both schemas, an input source's `ref` included. A `null` id
  pairs with either family.
- Every `$`-anchored id, name, currency, date and gap-path pattern refers to
  `one_line`, which refuses a newline, since Python's `re.search` lets `$`
  match before a final one.
- A source card's `about.name` holds its sources' names joined and clipped to
  512 characters, ending in an ellipsis.
- A `left_out` count may be of `pooled_entities`, the distinct parties of one
  pooled role across a step's inputs, named by its graph role in `role`, which
  only that `of` takes. `party_total` counts `pooled_entities` in every
  operation that lists it, one entry per pooled role.
- A card revision drawn from a dataset version may carry its version's
  `about.measure` and `about.fixed`, the version's fixed values other than
  those its measure names. A card revision drawn from a computed version, or a
  source card, carries neither. `about.measure` stays optional on a card, so
  a revision composed without it stays valid.
- `$defs/defined_ref`, a served reference with an optional `definition` of 1
  to 4096 characters. `about.fixed[].value` uses it on versions and cards, and
  `about.drawn.dimensions[]` gains optional `values`, 1 to 50, the values the
  card draws there with their definitions, present only when complete, with
  as many as the dimension's `count` and each `ref` once. The schema holds the
  bounds, and `check_description` raises `drawn_values_count_mismatch` or
  `duplicate_drawn_value` for the rest.
  `about.measure.definition` shares the same bound through `$defs/definition`.
- Vectors for a card without a measure, accepted, and rejections for an
  empty definition, one past 4096 characters, 51 drawn values, a fixed value
  without `label`, an empty `values` list, fewer `values` than `count`, a
  repeated drawn `ref` and a card drawn from a computed version carrying a
  measure. The card-revision and live-rendering vectors carry the measure,
  fixed values and drawn values with graph definitions.

[unreleased]: https://github.com/ONEcampaign/dfo-spec-kit/commits/main/python
