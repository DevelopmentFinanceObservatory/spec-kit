"""Canonical Arrow-backed frame materialisation and frame-byte serialisation."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import pyarrow as pa
import rfc8785
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar._unit_codec import (
    _arrow_unit_to_cell,
    _unit_to_arrow,
    _unit_type,
)
from dfo_frame_grammar.columnar import (
    _grain_columns,
    _sort_and_check,
    collation_sort_keys,
)
from dfo_frame_grammar.grammar import FrameError

__all__ = [
    "Frame",
    "canonical_frame_bytes",
    "canonical_frame_schema",
    "stream_canonical_frame_bytes",
    "unit_cell_array",
    "widen_frame_table",
]

# How many rows `stream_canonical_frame_bytes` keys and serialises at once,
# across chunk boundaries. Bounds the Python-object peak to one slice and
# pays the per-slice cost of `collation_sort_keys` once per this many rows,
# however small the chunks. `to_pylist()` on a slice gives the same rows in
# the same order as the matching part of `to_pylist()` on the whole table.
_STREAM_SLICE_ROWS = 8192


def _normalize_ref(value: object, *, description: str) -> str:
    if not isinstance(value, str):
        raise FrameError(
            "schema_violation", f"{description} must be a string, got {type(value)!r}"
        )
    text = unicodedata.normalize("NFC", value)
    grammar.reject_lone_surrogates(text)
    if not text:
        raise FrameError(
            "schema_violation", f"{description} must be a non-empty string"
        )
    return text


def _normalize_stat_var(value: object) -> str:
    text = _normalize_ref(value, description="stat_var")
    grammar.validate_stat_var_ref(text)
    return text


def _normalize_optional_ref(value: object, *, description: str) -> str | None:
    return None if value is None else _normalize_ref(value, description=description)


def _normalize_time_period(value: object) -> str:
    if not isinstance(value, str):
        raise FrameError(
            "schema_violation", f"time_period must be a string, got {type(value)!r}"
        )
    text = unicodedata.normalize("NFC", value)
    grammar.reject_lone_surrogates(text)
    grammar.validate_time_period(text)
    return text


def _normalize_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameError(
            "schema_violation", f"value must be an int or float, got {type(value)!r}"
        )
    try:
        result = float(value)
    except OverflowError as exc:
        raise FrameError(
            "non_finite_number", "value overflows the finite double domain"
        ) from exc
    if not math.isfinite(result):
        raise FrameError(
            "non_finite_number", f"value must be a finite number, got {value!r}"
        )
    return 0.0 if result == 0 else result


def canonical_frame_schema(columns: tuple[str, ...]) -> pa.Schema:
    """Return the physical schema shared by both public builders."""
    fields: list[pa.Field] = []
    for column in columns:
        if column == grammar.VALUE_COLUMN:
            field_type = pa.float64()
        elif column == grammar.UNIT_COLUMN:
            field_type = _unit_type()
        elif column == grammar.STATUS_COLUMN:
            field_type = pa.null()
        else:
            field_type = pa.string()
        fields.append(pa.field(column, field_type, nullable=True))
    return pa.schema(fields)


def unit_cell_array(cell: grammar.UnitCell | None, length: int) -> pa.Array:
    """`length` rows of `cell` in the canonical `unit` column type, the
    column a frame whose every row holds `cell` carries.

    Raises:
        FrameError: `invalid_unit` when `cell` fails
            `grammar.validate_unit_cell`.
    """
    grammar.validate_unit_cell(cell)
    unit_type = _unit_type()
    if cell is None:
        return pa.nulls(length, type=unit_type)
    return pa.repeat(pa.scalar(_unit_to_arrow(cell), type=unit_type), length)


def widen_frame_table(table: pa.Table) -> pa.Table:
    """Return a stored canonical frame table under the current canonical
    schema.

    A table already under `canonical_frame_schema` of its own column names
    is returned as it is. A table under that schema with the `unit` column
    in the two-member layout `struct<ref, request>`, which holds no
    composed unit, is returned with the column in the current layout and
    every cell unchanged, so it passes `Frame.from_table` and yields the
    same canonical bytes.

    Raises:
        FrameError: `schema_violation` for a table under any other schema.
    """
    columns = tuple(table.schema.names)
    roles = [
        column for column in columns if column not in grammar._RESERVED_COLUMN_NAMES
    ]
    schema = canonical_frame_schema(columns)
    position = schema.get_field_index(grammar.UNIT_COLUMN)
    unit_type = _unit_type()
    two_member = pa.field(
        grammar.UNIT_COLUMN,
        pa.struct([unit_type.field("ref"), unit_type.field("request")]),
    )
    derived = columns == grammar.derive_columns(roles)
    if derived and table.schema == schema:
        return table
    if not derived or table.schema != schema.set(position, two_member):
        raise FrameError(
            "schema_violation",
            f"table schema {table.schema!r} is not a canonical frame schema "
            "under either unit layout",
        )
    unit = table.column(position).combine_chunks()
    assert isinstance(unit, pa.StructArray)
    widened = pa.StructArray.from_arrays(
        [
            unit.field("ref"),
            unit.field("request"),
            pa.nulls(len(unit), type=unit_type.field("composed").type),
        ],
        fields=list(unit_type),
        mask=unit.is_null(),
    )
    return table.set_column(position, schema.field(position), widened)


def _table_from_rows(
    *, columns: tuple[str, ...], rows: Sequence[tuple[object, ...]]
) -> pa.Table:
    schema = canonical_frame_schema(columns)
    arrays: list[pa.Array] = []
    for index, column in enumerate(columns):
        cells = [row[index] for row in rows]
        if column == grammar.UNIT_COLUMN:
            converted = {
                cell: _unit_to_arrow(cast("grammar.UnitCell | None", cell))
                for cell in set(cells)
            }
            cells = [converted[cell] for cell in cells]
        arrays.append(pa.array(cells, type=schema.field(column).type))
    return pa.Table.from_arrays(arrays, schema=schema)


def _python_rows(
    columns: tuple[str, ...], table: pa.Table
) -> tuple[tuple[object, ...], ...]:
    """`table`'s rows as tuples of Python cells in `columns` order, with
    each `unit` struct converted back to its hashable cell."""
    return tuple(
        tuple(
            _arrow_unit_to_cell(row[column])
            if column == grammar.UNIT_COLUMN
            else row[column]
            for column in columns
        )
        for row in table.to_pylist()
    )


@dataclass(frozen=True, eq=False)
class Frame:
    """A validated frame whose canonical representation is a PyArrow table."""

    columns: tuple[str, ...]
    table: pa.Table

    @property
    def rows(self) -> tuple[tuple[object, ...], ...]:
        """Every row as a tuple of Python cells in `columns` order.

        Materialises the whole table as Python objects on every access, at
        several times the table's own memory. Columnar consumers read
        `table` instead.
        """
        return _python_rows(self.columns, self.table)

    @property
    def num_rows(self) -> int:
        """The number of rows, read from `table` without materialising them."""
        return self.table.num_rows

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Frame)
            and self.columns == other.columns
            and self.table.equals(other.table)
        )

    def __hash__(self) -> int:
        """Hash `columns`, the row count, and the first, middle and last
        rows, read by position with `table.slice` and converted as `rows`
        converts them.

        Equal frames hold equal cells at every position whatever their
        chunking, so they hash equal. Frames of one shape that differ in
        a sampled row hash apart. Reads three rows, not the table.
        """
        count = self.table.num_rows
        positions = sorted({0, count // 2, count - 1}) if count else []
        sample = tuple(
            row
            for position in positions
            for row in _python_rows(self.columns, self.table.slice(position, 1))
        )
        return hash((self.columns, count, sample))

    @classmethod
    def from_table(cls, *, columns: tuple[str, ...], table: pa.Table) -> Frame:
        """Wrap a table that already holds a canonical frame.

        Only for a table this package built that the caller stored and has
        verified since, such as `frame.table` written to Parquet by the
        same service and read back after its object checksum matched. The
        table must carry exactly `canonical_frame_schema(columns)`, and
        `columns` must be a derived column tuple; nothing else is checked.
        Cell contents are trusted: a null grain cell, a non-NFC string, a
        NaN or `-0.0` value, a unit struct with no variant or more than
        one, or a `composed` member that is not the canonical JCS text of a
        composed unit passes here and serialises wrongly or fails later
        with an error other than `FrameError`. Rows out of the order
        `columnar.collation_sort_keys` gives, or with equal grains, make
        `canonical_frame_bytes` and `frame_sha256` raise
        `row_key_not_increasing`. Every other table, including any
        from outside the service, goes through `build_frame_from_arrow`.

        Raises:
            FrameError: `schema_violation` when `columns` is not what
                `grammar.derive_columns` gives for its own role columns, or
                the table's schema differs from `canonical_frame_schema`.
        """
        roles = [
            column for column in columns if column not in grammar._RESERVED_COLUMN_NAMES
        ]
        if columns != grammar.derive_columns(roles):
            raise FrameError(
                "schema_violation",
                f"columns {list(columns)!r} are not a derived frame column order",
            )
        expected = canonical_frame_schema(columns)
        if table.schema != expected:
            raise FrameError(
                "schema_violation",
                f"table schema {table.schema!r} is not the canonical frame "
                f"schema {expected!r}",
            )
        return cls(columns=columns, table=table)

    @classmethod
    def _from_validated(cls, *, columns: tuple[str, ...], table: pa.Table) -> Frame:
        """`from_table` for this package's builders, whose `columns` come
        from `grammar.derive_columns` already."""
        if table.schema != canonical_frame_schema(columns):
            raise ValueError("validated frame table does not have the canonical schema")
        return cls(columns=columns, table=table)

    @classmethod
    def build(
        cls,
        *,
        role_keys: Iterable[str],
        rows: Iterable[Mapping[str, object]],
    ) -> Frame:
        """Validate mapping rows and finish in the canonical Arrow schema.

        The rows go into one table as given, which `columnar.collation_sort_keys`
        then sorts and checks for duplicate grains, as in
        `build_frame_from_arrow`.

        Raises:
            FrameError: `duplicate_row_key` when two rows share a grain, or
                the category of the first cell that fails validation.
        """
        role_keys = tuple(role_keys)
        role_columns = grammar.normalize_role_keys(role_keys)
        columns = grammar.derive_columns(role_keys)
        required = {
            grammar.STAT_VAR_COLUMN,
            grammar.TIME_PERIOD_COLUMN,
            grammar.VALUE_COLUMN,
            *role_columns,
        }
        allowed = required | {grammar.UNIT_COLUMN, *grammar.FACET_IDENTITY_COLUMNS}

        built_rows: list[tuple[object, ...]] = []
        for raw_row in rows:
            raw_key_by_norm: dict[str, str] = {}
            row: dict[str, object] = {}
            for raw_key, cell in raw_row.items():
                if not isinstance(raw_key, str):
                    raise FrameError(
                        "schema_violation",
                        f"row key must be a string, got {type(raw_key)!r}",
                    )
                key = unicodedata.normalize("NFC", raw_key)
                if key in raw_key_by_norm and raw_key_by_norm[key] != raw_key:
                    raise FrameError(
                        "schema_violation",
                        f"row keys {raw_key!r} and {raw_key_by_norm[key]!r} "
                        "collide after NFC normalisation",
                    )
                raw_key_by_norm[key] = raw_key
                row[key] = cell
            if grammar.STATUS_COLUMN in row:
                raise FrameError(
                    "schema_violation", "status is reserved and may not be supplied"
                )
            unknown = set(row) - allowed
            if unknown:
                raise FrameError(
                    "schema_violation",
                    f"row carries unknown column(s): {sorted(unknown)!r}",
                )
            missing = required - set(row)
            if missing:
                raise FrameError(
                    "schema_violation",
                    f"row is missing required column(s): {sorted(missing)!r}",
                )
            cells: dict[str, object] = {
                grammar.STAT_VAR_COLUMN: _normalize_stat_var(
                    row[grammar.STAT_VAR_COLUMN]
                ),
                grammar.TIME_PERIOD_COLUMN: _normalize_time_period(
                    row[grammar.TIME_PERIOD_COLUMN]
                ),
                grammar.VALUE_COLUMN: _normalize_value(row[grammar.VALUE_COLUMN]),
                grammar.UNIT_COLUMN: grammar.unit_cell_from_json(
                    row.get(grammar.UNIT_COLUMN)
                ),
                grammar.STATUS_COLUMN: None,
            }
            for role in role_columns:
                cells[role] = _normalize_ref(row[role], description=f"role {role!r}")
            for axis in grammar.FACET_IDENTITY_COLUMNS:
                cells[axis] = _normalize_optional_ref(
                    row.get(axis), description=f"facet {axis!r}"
                )
            built_rows.append(tuple(cells[column] for column in columns))

        table = _table_from_rows(columns=columns, rows=built_rows)
        return cls._from_validated(
            columns=columns, table=_sort_and_check(table, columns=columns)
        )


def _unit_to_json(value: object) -> object:
    return grammar.unit_cell_to_json(_arrow_unit_to_cell(value))


def _row_cells(frame: Frame, table_row: Mapping[str, object]) -> list[object]:
    """One row's cells in canonical column order, with the ``unit`` column
    resolved to its JCS-ready form (`_unit_to_json`) and every other column
    passed through unchanged -- the per-row projection
    `stream_canonical_frame_bytes` serialises.
    """
    return [
        _unit_to_json(table_row[column])
        if column == grammar.UNIT_COLUMN
        else table_row[column]
        for column in frame.columns
    ]


def canonical_frame_bytes(frame: Frame) -> bytes:
    """Return the frame's canonical JCS document bytes.

    Built by `stream_canonical_frame_bytes` over the one frame, so no more
    than `_STREAM_SLICE_ROWS` rows exist as Python objects at once and
    `Frame.rows` is never read.

    Raises:
        FrameError: `row_key_not_increasing` when the table's rows are not
            in strictly increasing `columnar.collation_sort_keys` order,
            which is `row_sort_key` order. `Frame.build` and
            `build_frame_from_arrow` sort by those keys; only a table
            wrapped by `Frame.from_table` or the `Frame` constructor can
            break the order.
    """
    return b"".join(stream_canonical_frame_bytes([frame]))


def stream_canonical_frame_bytes(chunks: Iterable[Frame]) -> Iterator[bytes]:
    """Yield JCS byte pieces that, concatenated in order, are the canonical
    document ``{"columns": [...], "frame_version": ..., "rows": [...]}`` of
    the logical frame formed by concatenating ``chunks``' rows in order.
    `canonical_frame_bytes` is this function over a single chunk.

    Rows are keyed and serialised in slices of `_STREAM_SLICE_ROWS` that
    span chunk boundaries. Chunk tables are buffered until they hold a
    full slice or the input ends, so at most one slice of rows exists as
    Python objects at once, and the buffered Arrow tables hold fewer than
    `_STREAM_SLICE_ROWS` rows plus one chunk. A chunk's rows can therefore
    be yielded after later chunks have been read. Every row before a
    refused one is still checked and yielded before the refusal.

    Each chunk is a fully validated `Frame`, never a raw PyArrow record
    batch or row dict: every cell this function serialises has already
    passed through `Frame.build`'s normalisers (NFC, `-0.0` collapse,
    int/float convergence) exactly once, rather than a second path that
    could drift from them.

    A later chunk whose `columns` differs from the first chunk's raises
    `FrameError("column_mismatch", ...)` once the buffered rows before it
    have streamed. Each slice's grain is keyed with
    `columnar.collation_sort_keys`, the keys both builders sort by, which
    order rows as `row_sort_key` does. A row whose key is not strictly
    greater than the previous row's -- checked both within a chunk and
    across a chunk boundary, so a chunk assembled by some means other than
    the builders cannot silently produce out-of-order or duplicate-keyed
    streamed bytes -- raises `FrameError("row_key_not_increasing", ...)`;
    equal keys refuse, the same rule `Frame.build` applies to one frame's
    own rows, extended across the whole stream. An empty ``chunks``
    iterable raises `FrameError("schema_violation", ...)`: with zero
    chunks there is no frame whose `columns` this function could emit.

    Relies on the canonical document's JCS shape: sorted
    top-level keys put `rows` last, so
    ``rfc8785.dumps({...,"rows":[]})`` minus its closing ``]}`` is exactly
    the prefix every row byte sequence is appended after, and JCS
    serialises each array element independently, so `rfc8785.dumps(row)`
    for one row is byte-identical to that row's own slice of the
    all-at-once document.
    """
    columns: tuple[str, ...] | None = None
    last_key: tuple | None = None
    pending: list[pa.Table] = []
    pending_rows = 0

    for frame in chunks:
        if columns is None:
            columns = frame.columns
            prefix = canonicalize(
                {
                    "frame_version": grammar.FRAME_VERSION,
                    "columns": list(columns),
                    "rows": [],
                }
            )
            # Sorted top-level keys put "rows" last, so the empty-rows
            # document always ends in the two bytes `]}`; stripping them
            # leaves the exact prefix every row byte sequence is appended
            # after (see this function's docstring).
            assert prefix.endswith(b"]}")
            yield prefix[:-2]
        elif frame.columns != columns:
            # Stream the buffered rows first, so a disorder among them
            # refuses as `row_key_not_increasing` before this chunk is.
            if pending_rows:
                yield from _stream_rows(
                    Frame(columns=columns, table=pa.concat_tables(pending)), last_key
                )
            raise FrameError(
                "column_mismatch",
                f"chunk columns {list(frame.columns)!r} differ from the "
                f"stream's columns {list(columns)!r}",
            )
        pending.append(frame.table)
        pending_rows += frame.table.num_rows
        if pending_rows < _STREAM_SLICE_ROWS:
            continue
        table = pa.concat_tables(pending)
        full_rows = pending_rows - pending_rows % _STREAM_SLICE_ROWS
        last_key = yield from _stream_rows(
            Frame(columns=columns, table=table.slice(0, full_rows)), last_key
        )
        pending = [table.slice(full_rows)]
        pending_rows -= full_rows

    if columns is None:
        raise FrameError(
            "schema_violation",
            "stream_canonical_frame_bytes requires at least one chunk to "
            "determine the frame's columns",
        )
    if pending_rows:
        yield from _stream_rows(
            Frame(columns=columns, table=pa.concat_tables(pending)), last_key
        )
    yield b"]}"


def _stream_rows(
    frame: Frame, last_key: tuple | None
) -> Generator[bytes, None, tuple | None]:
    """Yield the bytes of `frame`'s rows for `stream_canonical_frame_bytes`,
    each preceded by a comma unless it is the stream's first row, and
    return the last row's key.

    Raises:
        FrameError: `row_key_not_increasing` when a row's key is not
            strictly greater than the one before it, starting from
            `last_key`.
    """
    grain = _grain_columns(frame.columns)
    table = frame.table
    for slice_start in range(0, table.num_rows, _STREAM_SLICE_ROWS):
        row_slice = table.slice(slice_start, _STREAM_SLICE_ROWS)
        # One (rank, payload) pair per grain column, flattened into one
        # tuple per row; Python compares the tuples in the order Arrow
        # sorts the key columns.
        keys = collation_sort_keys(row_slice, grain)
        row_keys = zip(*(column.to_pylist() for column in keys.columns), strict=True)
        for table_row, key in zip(row_slice.to_pylist(), row_keys, strict=True):
            if last_key is not None and key <= last_key:
                raise FrameError(
                    "row_key_not_increasing",
                    "a row's row_sort_key is not strictly greater than "
                    "the previous row's, within or across chunks",
                )
            if last_key is not None:
                yield b","
            yield rfc8785.dumps(_row_cells(frame, table_row))
            last_key = key
    return last_key
