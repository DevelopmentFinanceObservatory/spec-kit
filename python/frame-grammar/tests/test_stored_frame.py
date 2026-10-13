"""A frame with no recipe stores as kit 959f8bc stored it, before the
`recipe` member, and a frame with a recipe stores in the recipe layout.

`fixtures/stored_frame/frame.parquet` holds the Parquet bytes 959f8bc wrote
for the rows in `write_stored_frame.py`, and `frame.json` its hashes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from dfo_frame_grammar import Frame, canonical_frame_bytes, canonical_frame_schema
from dfo_frame_grammar.content_version import frame_sha256, mint_content_version
from tests.fixtures.stored_frame.write_stored_frame import (
    ROLE_KEYS,
    ROWS,
    build_frame,
    parquet_bytes,
)

_STORED = Path(__file__).parent / "fixtures" / "stored_frame"
_RECORD = json.loads((_STORED / "frame.json").read_text())


def _read(data: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(data))


def test_a_frame_without_a_recipe_writes_the_stored_bytes() -> None:
    assert pa.__version__ == _RECORD["pyarrow_version"], (
        "the stored bytes were written under another pyarrow; regenerate them "
        "as write_stored_frame.py says"
    )
    stored = (_STORED / "frame.parquet").read_bytes()
    assert parquet_bytes(build_frame()) == stored


def test_the_stored_file_reads_as_the_frame_with_its_content_version() -> None:
    frame = build_frame()
    table = _read((_STORED / "frame.parquet").read_bytes())
    read = Frame.from_table(columns=frame.columns, table=table)
    assert read == frame
    digest = frame_sha256(read)
    assert digest == _RECORD["frame_sha256"]
    assert mint_content_version(frame_sha256=digest) == _RECORD["content_version"]


def test_a_frame_with_a_recipe_stores_and_reads_in_the_recipe_layout() -> None:
    recipe = {"currency": "EUR", "prices": "current", "recipe": "recipe/eur"}
    frame = Frame.build(role_keys=ROLE_KEYS, rows=[*ROWS, {**ROWS[2], "unit": recipe}])
    recipe_schema = canonical_frame_schema(frame.columns, recipe=True)
    assert frame.table.schema == recipe_schema
    table = _read(parquet_bytes(frame))
    assert table.schema == recipe_schema
    read = Frame.from_table(columns=frame.columns, table=table)
    assert read == frame
    assert canonical_frame_bytes(read) == canonical_frame_bytes(frame)
