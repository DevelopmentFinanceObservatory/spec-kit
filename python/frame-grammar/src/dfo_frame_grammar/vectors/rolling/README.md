# python/frame-grammar/src/dfo_frame_grammar/vectors/rolling

`rolling-vectors.json` records what `dfo_frame_grammar.rolling` returns, so a service that resolves a relative period in another language or another repository can check it lands on the same period. It ships as package data, like the frame vectors in `../frame/`. `python/frame-grammar/tests/test_rolling.py` runs the implementation against every vector.

The frame vector suite in `../frame/` is a separate suite. Its record schema, `tools/validate_frame_vectors.py`, and the digest guard in `tests/test_vectors.py` read that directory only.

## Format

The file is one JSON object.

| Field            | Meaning                                                                     |
| ---------------- | --------------------------------------------------------------------------- |
| `format_version` | The version of this record format, `1`.                                     |
| `anchor_rule`    | The value of `ANCHOR_RULE` the `latest_anchor` vectors were recorded under. |
| `latest_anchor`  | Vectors for `latest_anchor`.                                                |
| `shift_period`   | Vectors for `shift_period`.                                                 |

Every vector has a unique kebab-case `name` and exactly one of `expected` and `error`. `error` is the refusal the call raises: `rolling_anchor_unresolved` or `rolling_grain_unsupported` (the `code` of the `RollingError` raised), or `invalid_time_period` (the `category` of the `FrameError` that `parse_time_period` raises).

A `latest_anchor` vector carries `description` and `populations`, a list with one object per population mapping a series key to the period labels that series holds. Its `expected` is `{"period", "left_out"}`, where `left_out` lists `[population index, series key]` pairs in population order and then in the series order of the population's object.

A `shift_period` vector carries `period` and `offset`, and its `expected` is the shifted period label. `offset` is an integer. `shift_period` raises `TypeError` for anything else, so a caller that reads `-1.0` from JSON converts it first.

## Adding a vector

Add the record to the right list and run `uv run pytest tests/test_rolling.py` from `python/frame-grammar/`. A change to an existing vector's `expected` or `error` is a change to what "latest" means, so it needs a new `ANCHOR_RULE` value.
