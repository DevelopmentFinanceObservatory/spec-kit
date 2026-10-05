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
separator hashed into every canonical frame document and every
`content_version` preimage, and only a byte-rule generation change ever
moves it. `FRAME_GRAMMAR_VERSION` is this document's own published semver
-- it moves on every revision to `docs/frame-grammar-v1.md`, including a
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
)
from dfo_frame_grammar.content_version import (
    ContentVersionError,
    FrameTrace,
    frame_sha256,
    mint_collection_content_version,
    mint_content_version,
    recompute_content_version,
    stream_frame_sha256,
)
from dfo_frame_grammar.frame import (
    Frame,
    canonical_frame_bytes,
    canonical_frame_schema,
    stream_canonical_frame_bytes,
)
from dfo_frame_grammar.grammar import (
    FACET_IDENTITY_COLUMNS,
    FRAME_VERSION,
    STAT_VAR_COLUMN,
    STATUS_COLUMN,
    TIME_PERIOD_COLUMN,
    UNIT_COLUMN,
    VALUE_COLUMN,
    FrameError,
    TimeGrain,
    TimePeriod,
    derive_columns,
    parse_time_period,
)
from dfo_frame_grammar.version import FRAME_GRAMMAR_VERSION

__all__ = [
    "FACET_IDENTITY_COLUMNS",
    "FRAME_GRAMMAR_VERSION",
    "FRAME_VERSION",
    "STATUS_COLUMN",
    "STAT_VAR_COLUMN",
    "TIME_PERIOD_COLUMN",
    "UNIT_COLUMN",
    "VALUE_COLUMN",
    "ContentVersionError",
    "Frame",
    "FrameError",
    "FrameTrace",
    "TimeGrain",
    "TimePeriod",
    "UnitCodes",
    "array_kernel",
    "build_frame_from_arrow",
    "call_kernel",
    "canonical_frame_bytes",
    "canonical_frame_schema",
    "collation_keys",
    "collation_sort_keys",
    "derive_columns",
    "frame_sha256",
    "map_units",
    "mint_collection_content_version",
    "mint_content_version",
    "parse_time_period",
    "read_frame_csv",
    "recompute_content_version",
    "scalar_kernel",
    "stream_canonical_frame_bytes",
    "stream_frame_sha256",
    "unit_cells",
    "unit_codes",
]
