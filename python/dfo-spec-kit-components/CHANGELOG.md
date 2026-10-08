# Changelog

All notable changes to the `dfo-spec-kit-components` Python package will be
documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Relative periods in the write-side intent grammar
  (`schemas/intent.v0.json`). `$defs.relative_period` is
  `{anchor: "latest", offset}` with an integer `offset` of 0 or less.
  `window.start`, `window.end`, and each `change.basis` period of a
  `stat_card` admit a period label or a relative period. Every intent the
  grammar admitted before still validates. Resolved payloads carry period
  labels only, so the component schemas are unchanged. The fixtures
  `fixtures/line_chart/intent_relative_window.json` and
  `fixtures/stat_card/intent_relative_change.json` show both uses.
- `retry_modes` in `manifest.json` gains `refresh_not_permitted` and
  `render_target_mismatch`, both `do_not_retry`: the refusals the render
  routes (`presentation.render` in the code registry) add to the Presentation
  surface.
- A composed unit in the `unit` slot of `stat_card`, of each `line_chart`
  and `bar_chart` series and of each `table` row: `$defs.composed_unit`, a
  closed object whose required `numerator` and `denominator` state the unit
  of a quotient of two measures. A part (`$defs.unit_part`) is a string of
  1 to 200 characters, a `structured_unit`, or a `counted_stat_var`, and is
  never null and never a composed unit. `$defs.counted_stat_var` stands for
  a measure that declares no unit: a closed object with `stat_var`, a
  `dcid:`-prefixed reference, and `label`, the text a renderer prints. The
  slot admits four forms: null, a string, a `structured_unit` and a
  `composed_unit`. `fixtures/stat_card/payload_composed_unit.json` (US
  dollars per person) and `fixtures/table/payload_composed_unit.json`
  (constant 2023 US dollars per person) are worked payloads,
  and `fixtures/constraints/comparable_units_required/` carries a composed
  pair: `positive_composed.json`, two series with equal composed units, and
  `negative_composed_denominator_differs.json`, two series whose
  denominators name different StatVars.
- Optional `ref` (the Data Commons provenance dcid the entry was built from,
  for example `provenance/OECDCreditorReportingSystem`) on each
  `provenance.sources[]` item of the envelope schema, so a renderer can look
  the source up in the graph. It stays optional so components minted before
  platforms wrote it keep validating. `fixtures/bar_chart/payload.json`
  carries one.
- `$defs.observed_period` in the `bar_chart`, `stat_card` and `table`
  resolved schemas: a period point, or a closed span `start/end` whose ends
  share one grain (`2018/2022`, `2018-01/2022-12`), for a value derived from
  several periods. It types entity-mode `category_axis.period`, bar
  `points[].period`, stat card `period` and table `rows[].period`. A
  period-mode bar's points, `window.start`/`end`, `change.basis[]` and every
  `line_chart` period keep the point-only `period`. `bar_chart`'s series and
  point definitions are renamed `bar_series` and `bar_point`, since they no
  longer match `line_chart`'s `series` and `point`. The fixture
  `fixtures/bar_chart/payload_span.json` is an entity-mode bar over a
  `2018/2022` span.
- Optional `source` (`{id, name}`, the Data Commons Source node behind the
  provenance) and `data_commons` (`{id, name}`, with `id` one of `dfo`,
  `base`, `un_system`) on each `provenance.sources[]` item of the envelope
  schema. Both stay optional so dataset versions minted before the origin
  catalogue keep validating.
- Package scaffolding (uv, ruff, ty, pytest).
- The presentation catalogue bundle, shipped as package data at
  `src/dfo_spec_kit_components/catalogue/`: the envelope schema, the
  per-member component schemas (`line_chart`, `stat_card`), the write-side
  intent grammar (`schemas/intent.v0.json`), minimal and worked intents,
  resolved-payload fixtures, constraint fixtures, and the manifest digesting
  all of it, conformance-tested against the grammar, the envelope schema,
  and the manifest's own digest map.
- `dfo_spec_kit_components.bundle_dir()`: locates the installed bundle
  through `importlib.resources`, working the same from a repo checkout and
  from an installed wheel.
- An optional `series_id` on `table`'s row shape (`schemas/component/table.schema.json`),
  the series identity a row belongs to, matching the pattern `bar_chart` and
  `line_chart` use for their own series `id`.
- An optional `title` on `stat_card`'s resolved schema (`schemas/component/stat_card.schema.json`),
  the same `{text, authorship}` shape the chart components already carry, so a
  model-authored heading survives onto the card.
- Optional `scale` and `range` on `line_chart` and `bar_chart`, and an optional
  `change` on `stat_card`, in the write-side intent grammar
  (`schemas/intent.v0.json`), so a caller can author the value axis's scale and
  fixed bounds and a stat card's derived change between two observed periods.
  `change.derivation` defaults to `difference` when absent; `change.basis`
  orders its two periods `[from, to]`.

### Changed

- `catalog_version` moves from `2026.8.0` to `2026.10.0`, a minor change
  under `policies/component-spec.toml`: the intent grammar widens and two
  fixtures are added. A deployment on `2026.8.0` refuses an intent that
  carries a relative period, so a consumer comparing releases reads
  `2026.10.0` as the first that admits one. The payload fixtures carry the
  new `catalog_version`.

- `provenance.refreshed_from` in the envelope schema is described as the
  revision a payload was drawn from on newer data: the revision a render
  redrew without minting a new one, or the revision a refresh revision was
  minted from. Its type is unchanged.

- The intent grammar's `window.start` and `window.end`
  (`schemas/intent.v0.json`) admit 21 characters, up from 20, the longest
  period the frame grammar's `parse_time_period` accepts: a day-grain span
  such as `2024-01-01/2024-01-31`. A frame's coverage over such a span
  validates when a caller writes it back as the window. `change.basis[]`
  keeps 20, since a basis names point periods only.

- `bar_chart`'s resolved schema (`schemas/component/bar_chart.schema.json`) names
  its axes `category_axis` and `value_axis` in place of `x_axis` and `y_axis`,
  since orientation decides which axis renders horizontal and a fixed
  `x_axis`/`y_axis` pair is wrong for a horizontal bar. `line_chart` keeps
  `x_axis` and `y_axis`. `schema_version` stays 1; a consuming service's own
  migration rewrites its stored `bar_chart` payloads to the new key names.

### Changed

- `bar_chart`'s resolved schema (`schemas/component/bar_chart.schema.json`) names
  its axes `category_axis` and `value_axis` in place of `x_axis` and `y_axis`,
  since orientation decides which axis renders horizontal and a fixed
  `x_axis`/`y_axis` pair is wrong for a horizontal bar. `line_chart` keeps
  `x_axis` and `y_axis`. `schema_version` stays 1; a consuming service's own
  migration rewrites its stored `bar_chart` payloads to the new key names.

[unreleased]: https://github.com/ONEcampaign/dfo-spec-kit/commits/main/python
