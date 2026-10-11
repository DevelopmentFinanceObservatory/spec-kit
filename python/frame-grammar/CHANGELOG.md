# Changelog

All notable changes to the `dfo-spec-kit-frame-grammar` Python package will be
documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- The frame grammar implementation: `Frame` construction, canonical frame
  bytes, `content_version` minting and recompute, and collection-level
  `content_version` minting, conformance-tested against its packaged golden
  vector suite.
- `dfo_frame_grammar.arrow` builds `Frame` values from `pyarrow.Table` objects
  and grammar-shaped CSV data. Frame materialisations now retain their
  canonical Arrow table and expose `rows` as an on-demand compatibility view.
- An Arrow column type without a dictionary-encoding kernel, such as struct or
  list, raises `FrameError` `schema_violation` from `build_frame_from_arrow`.
- `Frame.from_table`, a public constructor for a table that already holds a
  canonical frame, checked against `canonical_frame_schema`, and
  `Frame.num_rows`.
- `dfo_frame_grammar.columnar`: `unit_codes`, `map_units` and `unit_cells` for
  the `unit` struct; `collation_keys` and `collation_sort_keys` for the
  grammar's row order; and `call_kernel`, `array_kernel` and `scalar_kernel`.
- The package root exports `build_frame_from_arrow`, `read_frame_csv`,
  `canonical_frame_schema`, and the `columnar` functions.
- `mint_content_version` and `FrameTrace` accept a manifest trace's
  `conversion` array as well as the bare conversion record. The array must
  hold exactly one entry; its `recipe`, `rate_table_version`, and
  `deflator_table_version` enter the preimage, and the trace-only `members`,
  `fallback`, and `projected` are dropped, so both spellings of one record
  mint the same `content_version`. Any other array length raises
  `ContentVersionError` `schema_violation`, and any other entry member raises
  `unknown_member`.
- `time_period` accepts an ISO 8601 interval `<start>/<end>` of two points at
  one grain with `start` before `end`, such as `2018/2022` or
  `2018-01/2022-12`. A one-period window is written as the point. An open end,
  a duration, the `--` separator, mixed grain, reversed or equal ends, and
  non-ASCII digits raise `FrameError` `invalid_time_period`. Ten frame vectors
  cover the accepted and refused forms. `FRAME_GRAMMAR_VERSION` stays `1.0.0`:
  the grammar is pre-release, so the interval form is defined in place.
- `parse_time_period` returns a `TimePeriod(start, end, grain)`, where a point
  has `start == end` and `grain` is a `TimeGrain` (`year`, `month`, or `day`).
  `dfo_frame_grammar.grammar` and the package root export all three.
- `dfo_frame_grammar.rolling`, exported from the package root, resolves a
  relative period `{anchor: "latest", offset: n}`. `latest_anchor` takes one
  mapping per population, from series key to the period labels the series
  holds, and returns an `Anchor`: the newest period that at least half of the
  series hold in every population (`ANCHOR_SHARE`, named `ANCHOR_RULE`
  `half1`), with the series that do not hold it. `shift_period` moves a year
  by years and a month by months, and moves a day by offset 0 only.
  `RollingAnchorUnresolved` (`rolling_anchor_unresolved`) is raised for no
  population, a population with no series, or no qualifying period, and
  `RollingGrainUnsupported` (`rolling_grain_unsupported`) for a span label,
  mixed grains, or a day asked to move. Both use `parse_time_period` for
  grain and order. `shift_period` raises `TypeError` for an offset that is
  not an `int`. `vectors/rolling/rolling-vectors.json` records 35 cases.
  `FRAME_GRAMMAR_VERSION` stays `1.0.0`: `docs/frame-grammar-v1.md` is
  unchanged.
- The `unit` cell takes a fourth form, the composed unit
  `{"numerator": <part>, "denominator": <part>}`, for a frame whose values are
  a quotient of two measures. A part is a unit ref string, a unit-request
  object, or `{"stat_var": "dcid:..."}`. `Frame.rows` gives the cell as a
  `ComposedUnit`, with a `StatVarPart` for a StatVar side. Any other shape,
  including a null or composed part, raises `FrameError` `invalid_unit`. Rows
  order a composed unit after a unit request, by its JCS bytes. Ten frame
  vectors cover the accepted and refused forms. `FRAME_GRAMMAR_VERSION` stays
  `1.0.0`, because the grammar is pre-release and the composed form is defined
  in place.
- The package root exports `UnitRequest`, `ComposedUnit`, `StatVarPart`, the
  aliases `UnitCell` and `UnitPart`, and three conversions:
  `unit_cell_from_json`, `unit_cell_to_json` and `unit_cell_array`.
  `unit_cell_array` refuses a hand-built cell that validation would not
  return, through `grammar.validate_unit_cell`, with `FrameError`
  `invalid_unit`.
- `widen_frame_table` takes a table whose `unit` column is the two-member
  struct and returns it under the current canonical schema.

### Changed

- Breaking. `content_version` is the hash of the canonical frame alone. The
  preimage is `{"identity_version": "cv2", "frame_sha256": ...}`, so one frame
  mints one token whatever lineage produced it. `mint_content_version` takes
  `frame_sha256` only and `recompute_content_version` takes the canonical
  bytes only; `graph_revision`, `source_revisions`, `facets` and `conversion`
  are no longer parameters. `FrameTrace` keeps its fields for a caller's own
  trace, and nothing in this package reads it. Every frame vector's
  `expected_preimage` and `expected_content_version` change, the vector record
  format moves to `format_version` 2 without `content_version_input`, the
  thirteen lineage `content-version-*` vectors and the two
  `frame-multi-source-multi-facet-*` vectors are removed, and three
  `content-version-*` digest vectors are added. `ContentVersionError` raises
  only `invalid_digest`; `unknown_member`, `invalid_facet_axis`,
  `invalid_facet_basis`, `invalid_facet_ref`, `duplicate_source`,
  `duplicate_axis` and `conversion_null` are retired.
  `grammar.validate_unit_request_ref` and the role-shaped `currency` it
  accepted are removed. `FRAME_GRAMMAR_VERSION` moves to `1.1.0`: frame bytes
  are unchanged and every token moves.
- Breaking. The collection-level `content_version` is removed:
  `mint_collection_content_version`, `COLLECTION_IDENTITY_VERSION` (`colcv1`),
  the `duplicate_spec_id`, `invalid_spec_id` and
  `invalid_member_content_version` refusal categories, the
  `collection_content_version` vector kind with its five `collection-*`
  vectors, and the "Collection-level `content_version`" section of
  `docs/frame-grammar-v1.md`. Datasets no longer serves collections, so no
  consumer mints one. Frame bytes and frame-level `content_version` tokens are
  unchanged.
- `validate_time_period` calls `parse_time_period` and raises what it raises.
- Breaking for Arrow consumers. The canonical `unit` column type gains a third
  member, `composed: string`, holding the JCS text of a composed unit. A
  column of 10,000 unit refs takes 4.1 bytes a row more than under the
  two-member `struct<ref, request>`. `Frame.from_table` refuses a table stored
  under the two-member layout until `widen_frame_table` has widened it;
  `build_frame_from_arrow` accepts both and returns the three-member layout.
  `build_frame_from_arrow` reads any JSON text of a composed unit and stores
  the JCS form, refuses other text with `FrameError` `invalid_unit`, and
  refuses a `composed` member whose Arrow type is neither a string type nor
  null with `schema_violation`. Canonical bytes, `frame_sha256` and
  `content_version` are unchanged for every frame.
- `unit_cells` returns `ComposedUnit` cells, and `collation_keys` ranks a
  composed unit 3, after a unit request at 2.
- `canonical_frame_bytes` and `frame_sha256` stream through
  `stream_canonical_frame_bytes`. Their bytes and digests are unchanged for
  every frame `Frame.build` or `build_frame_from_arrow` produces. A frame whose
  table rows are out of canonical order now raises `FrameError`
  `row_key_not_increasing`.
- `Frame.__hash__` hashes `columns`, the row count, and the first, middle and
  last rows, and no longer reads `Frame.rows`.
- `build_frame_from_arrow` normalises and sort-keys each distinct `unit` value
  once instead of once per row.
- `Frame.build` sorts and refuses duplicate grains with
  `collation_sort_keys`, as `build_frame_from_arrow` does, and
  `canonical_frame_bytes`, `frame_sha256` and `stream_canonical_frame_bytes`
  check row order with the same keys. A composed unit's key is its stored JCS
  text, and a unit request is serialised once per distinct request in each
  slice of 8,192 rows. Bytes, digests, refusal categories and messages are
  unchanged.

[unreleased]: https://github.com/ONEcampaign/dfo-spec-kit/commits/main/python
