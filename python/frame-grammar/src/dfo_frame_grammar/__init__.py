"""The frame materialisation library: the frame grammar, canonical frame
bytes, `content_version` minting and recompute, and the published
`FRAME_GRAMMAR_VERSION`.

This package publishes the grammar's executable form together with its
golden vector suite: see `docs/frame-grammar-v1.md` for the normative,
language-neutral specification and `src/dfo_frame_grammar/vectors/frame/` for
the conformance vectors.

Two version-shaped strings are exported here for entirely different jobs,
and a caller assembling a trace must pick the right one: `FRAME_VERSION`
(`"frame1"`, from `dfo_frame_grammar.grammar`) is the byte-rule domain
separator hashed into every canonical frame document, and only a byte-rule
generation change ever moves it. `FRAME_GRAMMAR_VERSION` is this document's own published semver
-- from the grammar's first release it moves on every revision to
`docs/frame-grammar-v1.md`, including a
prose-only patch that changes no byte rule at all, and it is never hashed
into anything; it travels on `FrameTrace.frame_grammar_version` purely as a
caller-facing label of which revision of the document described the code
that produced a given frame.

Dependency budget: the standard library, `pyarrow`, `rfc8785`, and
`dfo_spec_kit`. Arrow is required because a `Frame` retains its canonical
columnar table.
"""

from __future__ import annotations

from dfo_frame_grammar.arrow import build_frame_from_arrow, read_frame_csv
from dfo_frame_grammar.columnar import (
    UnitCodes,
    array_kernel,
    call_kernel,
    collation_keys,
    collation_sort_keys,
    map_units,
    scalar_kernel,
    unit_cells,
    unit_codes,
    unit_column_cells,
)
from dfo_frame_grammar.content_version import (
    ContentVersionError,
    FrameTrace,
    frame_sha256,
    mint_content_version,
    recompute_content_version,
    stream_frame_sha256,
)
from dfo_frame_grammar.frame import (
    Frame,
    canonical_frame_bytes,
    canonical_frame_schema,
    stream_canonical_frame_bytes,
    unit_cell_array,
    widen_frame_table,
)
from dfo_frame_grammar.grammar import (
    FACET_IDENTITY_COLUMNS,
    FRAME_VERSION,
    STAT_VAR_COLUMN,
    STATUS_COLUMN,
    TIME_PERIOD_COLUMN,
    UNIT_COLUMN,
    VALUE_COLUMN,
    ComposedUnit,
    FrameError,
    StatVarPart,
    TimeGrain,
    TimePeriod,
    UnitCell,
    UnitPart,
    UnitRequest,
    derive_columns,
    parse_time_period,
    unit_cell_from_json,
    unit_cell_to_json,
)
from dfo_frame_grammar.rolling import (
    ANCHOR_RULE,
    ANCHOR_SHARE,
    Anchor,
    RollingAnchorUnresolved,
    RollingError,
    RollingGrainUnsupported,
    latest_anchor,
    shift_period,
)
from dfo_frame_grammar.version import FRAME_GRAMMAR_VERSION

__all__ = [
    "ANCHOR_RULE",
    "ANCHOR_SHARE",
    "FACET_IDENTITY_COLUMNS",
    "FRAME_GRAMMAR_VERSION",
    "FRAME_VERSION",
    "STATUS_COLUMN",
    "STAT_VAR_COLUMN",
    "TIME_PERIOD_COLUMN",
    "UNIT_COLUMN",
    "VALUE_COLUMN",
    "Anchor",
    "ComposedUnit",
    "ContentVersionError",
    "Frame",
    "FrameError",
    "FrameTrace",
    "RollingAnchorUnresolved",
    "RollingError",
    "RollingGrainUnsupported",
    "StatVarPart",
    "TimeGrain",
    "TimePeriod",
    "UnitCell",
    "UnitCodes",
    "UnitPart",
    "UnitRequest",
    "array_kernel",
    "build_frame_from_arrow",
    "call_kernel",
    "canonical_frame_bytes",
    "canonical_frame_schema",
    "collation_keys",
    "collation_sort_keys",
    "derive_columns",
    "frame_sha256",
    "latest_anchor",
    "map_units",
    "mint_content_version",
    "parse_time_period",
    "read_frame_csv",
    "recompute_content_version",
    "scalar_kernel",
    "shift_period",
    "stream_canonical_frame_bytes",
    "stream_frame_sha256",
    "unit_cell_array",
    "unit_cell_from_json",
    "unit_cell_to_json",
    "unit_cells",
    "unit_codes",
    "unit_column_cells",
    "widen_frame_table",
]
