"""Canonical Arrow-backed frame materialisation and frame-byte serialisation."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import cast

import pyarrow as pa
import rfc8785
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar.grammar import FrameError

__all__ = [
    "Frame",
    "canonical_frame_bytes",
    "canonical_frame_schema",
    "stream_canonical_frame_bytes",
]

# How many rows' worth of Python objects `stream_canonical_frame_bytes`
# builds at once (via `pa.Table.slice(...).to_pylist()`) when serialising one
# chunk's table. Bounds the streaming form's per-chunk Python-object peak to
# one slice rather than the whole chunk, without changing the emitted bytes:
# `to_pylist()` on a slice is byte-for-byte the same rows in the same order
# as the corresponding slice of `to_pylist()` on the whole table.
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


def _normalize_unit(value: object) -> str | grammar.UnitRequest | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = unicodedata.normalize("NFC", value)
        grammar.reject_lone_surrogates(text)
        if not text:
            raise FrameError("schema_violation", "unit must be a non-empty string")
        return text
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise FrameError("schema_violation", "unit request keys must be strings")
        request = cast("Mapping[str, object]", value)
        grammar.validate_unit_request(request)
        return grammar.UnitRequest(
            currency=unicodedata.normalize("NFC", cast(str, request["currency"])),
            prices=cast(str, request["prices"]),
            base_year=cast("int | None", request.get("base_year")),
        )
    raise FrameError(
        "schema_violation",
        f"unit must be a string, mapping, or null, got {type(value)!r}",
    )


def _unit_request_type() -> pa.StructType:
    return pa.struct(
        [
            pa.field("currency", pa.string()),
            pa.field("prices", pa.string()),
            pa.field("base_year", pa.int64()),
        ]
    )


def _unit_type() -> pa.StructType:
    """The one nullable tagged representation of the unit union."""
    return pa.struct(
        [pa.field("ref", pa.string()), pa.field("request", _unit_request_type())]
    )


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


def _unit_to_arrow(cell: object) -> object:
    if cell is None:
        return None
    if isinstance(cell, str):
        return {"ref": cell, "request": None}
    assert isinstance(cell, grammar.UnitRequest)
    return {"ref": None, "request": cell.as_json()}


def _table_from_rows(
    *, columns: tuple[str, ...], rows: tuple[tuple[object, ...], ...]
) -> pa.Table:
    schema = canonical_frame_schema(columns)
    arrays: list[pa.Array] = []
    for index, column in enumerate(columns):
        cells = [row[index] for row in rows]
        if column == grammar.UNIT_COLUMN:
            cells = [_unit_to_arrow(cell) for cell in cells]
        arrays.append(pa.array(cells, type=schema.field(column).type))
    return pa.Table.from_arrays(arrays, schema=schema)


def _arrow_unit_to_cell(value: object) -> object:
    if value is None:
        return None
    assert isinstance(value, Mapping)
    tagged = cast("Mapping[str, object]", value)
    ref = tagged["ref"]
    request = tagged["request"]
    if ref is not None:
        return ref
    assert isinstance(request, Mapping)
    request = cast("Mapping[str, object]", request)
    return grammar.UnitRequest(
        currency=cast(str, request["currency"]),
        prices=cast(str, request["prices"]),
        base_year=cast("int | None", request["base_year"]),
    )


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
        NaN or `-0.0` value, or a unit struct with both or neither variant
        passes here and serialises wrongly or fails later with an error
        other than `FrameError`. Rows out of canonical order, or with
        equal grains, make `canonical_frame_bytes` and `frame_sha256`
        raise `row_key_not_increasing`. Every other table, including any
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
        """Validate mapping rows and finish in the canonical Arrow schema."""
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
                grammar.UNIT_COLUMN: _normalize_unit(row.get(grammar.UNIT_COLUMN)),
                grammar.STATUS_COLUMN: None,
            }
            for role in role_columns:
                cells[role] = _normalize_ref(row[role], description=f"role {role!r}")
            for axis in grammar.FACET_IDENTITY_COLUMNS:
                cells[axis] = _normalize_optional_ref(
                    row.get(axis), description=f"facet {axis!r}"
                )
            built_rows.append(tuple(cells[column] for column in columns))

        keyed = [(grammar.row_sort_key(columns, row), row) for row in built_rows]
        seen_keys: set[tuple] = set()
        for key, _ in keyed:
            if key in seen_keys:
                raise FrameError(
                    "duplicate_row_key",
                    "two rows share the same grain (every cell except value and status)",
                )
            seen_keys.add(key)
        keyed.sort(key=lambda item: item[0])
        sorted_rows = tuple(row for _, row in keyed)
        return cls._from_validated(
            columns=columns, table=_table_from_rows(columns=columns, rows=sorted_rows)
        )


def _unit_to_json(value: object) -> object:
    cell = _arrow_unit_to_cell(value)
    return cell.as_json() if isinstance(cell, grammar.UnitRequest) else cell


def _row_cells(frame: Frame, table_row: Mapping[str, object]) -> list[object]:
    """One row's cells in canonical column order, with the ``unit`` column
    resolved to its JCS-ready form (`_unit_to_json`) and every other column
    passed through unchanged -- the per-row projection
    `stream_canonical_frame_bytes` serialises and sort-keys.
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
            in strictly increasing `row_sort_key` order. `Frame.build` and
            `build_frame_from_arrow` always produce that order; only a
            table wrapped by `Frame.from_table` or the `Frame` constructor
            can break it.
    """
    return b"".join(stream_canonical_frame_bytes([frame]))


def stream_canonical_frame_bytes(chunks: Iterable[Frame]) -> Iterator[bytes]:
    """Yield JCS byte pieces that, concatenated in order, are the canonical
    document ``{"columns": [...], "frame_version": ..., "rows": [...]}`` of
    the logical frame formed by concatenating ``chunks``' rows in order --
    without ever holding more than one slice of `_STREAM_SLICE_ROWS` rows
    as Python objects at once. `canonical_frame_bytes` is this function
    over a single chunk.

    Each chunk is a fully validated `Frame`, never a raw PyArrow record
    batch or row dict: every cell this function serialises has already
    passed through `Frame.build`'s normalisers (NFC, `-0.0` collapse,
    int/float convergence) exactly once, rather than a second path that
    could drift from them.

    A later chunk whose `columns` differs from the first chunk's raises
    `FrameError("column_mismatch", ...)`. A row whose `row_sort_key` is
    not strictly greater than the previous row's -- checked both within a
    chunk and across a chunk boundary, so a chunk assembled by some means
    other than `Frame.build` cannot silently produce out-of-order or
    duplicate-keyed streamed bytes -- raises
    `FrameError("row_key_not_increasing", ...)`; equal keys refuse, the
    same rule `Frame.build` applies to one frame's own rows, extended
    across the whole stream. An empty ``chunks`` iterable raises
    `FrameError("schema_violation", ...)`: with zero chunks there is no
    frame whose `columns` this function could emit.

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
            raise FrameError(
                "column_mismatch",
                f"chunk columns {list(frame.columns)!r} differ from the "
                f"stream's columns {list(columns)!r}",
            )

        table = frame.table
        for slice_start in range(0, table.num_rows, _STREAM_SLICE_ROWS):
            # Sliced rather than read whole: `to_pylist()` over one bounded
            # slice at a time keeps at most `_STREAM_SLICE_ROWS` rows' worth
            # of Python dicts alive per chunk, instead of the whole chunk's.
            row_slice = table.slice(slice_start, _STREAM_SLICE_ROWS)
            for table_row in row_slice.to_pylist():
                cells = _row_cells(frame, table_row)
                # `row_sort_key`'s own `cell_sort_key` ranks a `UnitRequest`
                # and a plain `Mapping` identically -- both hash their JCS
                # bytes at rank 2 -- so the already-JSON-ified `unit` cell in
                # `cells` (a dict, once `_unit_to_json` has run) sorts
                # exactly where the pre-JSON `grammar.UnitRequest` cell
                # `Frame.build` sorted it.
                key = grammar.row_sort_key(frame.columns, cells)
                if last_key is not None and key <= last_key:
                    raise FrameError(
                        "row_key_not_increasing",
                        "a row's row_sort_key is not strictly greater than "
                        "the previous row's, within or across chunks",
                    )
                if last_key is not None:
                    yield b","
                yield rfc8785.dumps(cells)
                last_key = key

    if columns is None:
        raise FrameError(
            "schema_violation",
            "stream_canonical_frame_bytes requires at least one chunk to "
            "determine the frame's columns",
        )
    yield b"]}"
