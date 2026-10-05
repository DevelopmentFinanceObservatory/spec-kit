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

### Changed

- `validate_time_period` calls `parse_time_period` and raises what it raises.
- `canonical_frame_bytes` and `frame_sha256` stream through
  `stream_canonical_frame_bytes`. Their bytes and digests are unchanged for
  every frame `Frame.build` or `build_frame_from_arrow` produces. A frame whose
  table rows are out of canonical order now raises `FrameError`
  `row_key_not_increasing`.
- `Frame.__hash__` hashes `columns`, the row count, and the first, middle and
  last rows, and no longer reads `Frame.rows`.
- `build_frame_from_arrow` normalises and sort-keys each distinct `unit` value
  once instead of once per row.

[unreleased]: https://github.com/ONEcampaign/dfo-spec-kit/commits/main/python
