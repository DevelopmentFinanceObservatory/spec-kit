# dfo-spec-kit-frame-grammar (Python)

This package provides canonical frame bytes, `content_version` minting, and the published `FRAME_GRAMMAR_VERSION`. See the [repo README](../../README.md) for how this package fits with the rest of `dfo-spec-kit`, and `docs/frame-grammar-v1.md` for the normative specification this package implements.

The import name is `dfo_frame_grammar`. It depends on `dfo-spec-kit` (a path source to `../spec-kit`, developed together in this repo) for the identity canonicalisation primitives it reuses, plus `pyarrow` and `rfc8785` directly.

## Installation

This package uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run ty check src/dfo_frame_grammar
```

## Arrow-backed frame building

`dfo_frame_grammar.arrow` builds a `Frame` from a `pyarrow.Table` or CSV with grammar column names. It applies the same construction rules and `FrameError` categories as `Frame.build`.

Every `Frame` stores its canonical materialisation as a `pyarrow.Table`. `Frame.rows` materialises every row as Python objects on each access; columnar consumers read `frame.table` and `frame.num_rows`.

```python
import pyarrow as pa
from dfo_frame_grammar.arrow import build_frame_from_arrow, read_frame_csv

table = pa.table(
    {
        "stat_var": ["dcid:MalariaGrantDisbursements"],
        "provider": ["country/FRA"],
        "time_period": ["2015"],
        "value": [1000.0],
    }
)
frame = build_frame_from_arrow(table, role_keys=["provider"])

# CSV headers use the grammar's column names.
table = read_frame_csv(csv_bytes, role_keys=["provider"])
frame = build_frame_from_arrow(table, role_keys=["provider"])
```

## Columnar consumers

A consumer that keeps frames in Arrow uses the package root exports.

- `Frame.from_table(columns=..., table=...)` wraps a table this package built, such as `frame.table` written to Parquet and read back. It checks `columns` and the schema against `canonical_frame_schema(columns)`, accepts the `unit` column in every layout a release stored, and trusts the cells and row order. Untrusted tables go through `build_frame_from_arrow`.
- `canonical_frame_bytes` and `frame_sha256` serialise through `stream_canonical_frame_bytes`, 8,192 rows at a time. Hashing a 250,000-row frame raises peak RSS by about 4 MiB, against about 197 MiB for one whole-table document (`benchmarks/arrow_frame_rss.py`). A table whose rows are out of canonical order refuses with `FrameError` `row_key_not_increasing`; only `Frame.from_table` or the `Frame` constructor can produce one.
- `unit_codes(unit)` gives each row an integer code, equal exactly where the `unit` values are equal, read from the struct's leaf dictionary codes. PyArrow cannot `dictionary_encode`, `unique` or `group_by` the `unit` struct itself. `UnitCodes.count` is the number of distinct units, `unit_cells` their values, and `map_units` applies a function once per distinct unit.
- A non-null `unit` cell is a `UnitCell`: a ref string, a `UnitRequest` (`currency`, `prices`, `base_year` and an optional `recipe`), or a `ComposedUnit` whose `numerator` and `denominator` are each a ref string, a `UnitRequest`, or a `StatVarPart`. `unit_cell_from_json(value)` validates the JSON form and returns the cell, `unit_cell_to_json(cell)` gives the JSON form back, and `unit_cell_array(cell, length)` builds `length` rows of the cell in the canonical `unit` column type. Both take and return `None` for a null unit. `unit_cell_array` refuses a hand-built cell that validation would not return with `FrameError` `invalid_unit`. A reader on frame grammar `1.0.0` refuses a cell that carries `recipe`, so every reader takes `1.1.0` before any producer writes one.
- The `unit` column is `struct<ref, request, composed>`. Its `request` struct gains a `recipe` child only in a frame where some cell carries a recipe, so a frame without one stores as frame grammar `1.0.0` stored it and a `1.0.0` reader reads it. `canonical_frame_schema(columns)` is that layout and `canonical_frame_schema(columns, recipe=True)` the recipe layout. `composed` is a string holding the composed unit's JCS text, so `unit_codes` and `collation_keys` read it as one leaf, and a column with no composed unit spends about 4 bytes a row on it (`benchmarks/unit_column_bytes.py`).
- `widen_frame_table(table, recipe=False)` takes a canonical table whose `unit` column is in any layout a release stored, `struct<ref, request>` or `struct<ref, request, composed>` with or without `recipe`, and returns it in the recipe layout when `recipe` is true or some cell carries a recipe, and in the layout without `recipe` otherwise. `Frame.from_table` applies it, so a reader never calls it first. A table already in the result's layout comes back unchanged, and any other schema, a table with no `unit` column included, refuses with `FrameError` `schema_violation`.
- A streaming writer fixes its Parquet schema before the first chunk. It passes `recipe=True` to `canonical_frame_schema` when its own plan attaches a recipe to some cell, and puts each chunk in that layout with `widen_frame_table(chunk.table, recipe=True)`; otherwise it writes the default layout, and `pyarrow`'s writer refuses a chunk that carries a recipe. `stream_canonical_frame_bytes` takes chunks in either layout.
- `collation_keys` and `collation_sort_keys` give a rank and a payload array per column. Sorting ascending by them orders rows as `Frame.build` does, with nulls first and strings by code point. In the `unit` column a ref sorts before a unit request, and a unit request before a composed unit.
- `call_kernel`, `array_kernel` and `scalar_kernel` call `pyarrow.compute` functions that PyArrow's type stubs omit.

```python
import pyarrow.parquet as pq
from dfo_frame_grammar import Frame, array_kernel, collation_sort_keys, unit_codes

frame = Frame.from_table(columns=columns, table=pq.read_table(path))
if unit_codes(frame.table.column("unit")).count > 1:
    ...  # the frame mixes units

keys = collation_sort_keys(frame.table, ["stat_var", "provider", "time_period"])
order = array_kernel(
    "sort_indices", keys, sort_keys=[(name, "ascending") for name in keys.column_names]
)
```

## Golden vector suite

`src/dfo_frame_grammar/vectors/frame/` ships with the package as the frame grammar's language-neutral conformance suite. Its README defines the record format.

```bash
# Validate the vectors' own internal consistency, without importing this package
uv run ../../tools/validate_frame_vectors.py

# Drive the real implementation against every vector
uv run pytest tests/test_vectors.py
```
