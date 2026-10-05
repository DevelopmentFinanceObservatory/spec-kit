"""Grammar-aware Arrow kernels for frame construction and columnar consumers.

`Frame.build` and `build_frame_from_arrow` sort, deduplicate, and read the
`unit` column through these functions, so a consumer computing on
`Frame.table` that uses them orders rows and identifies units exactly as
frame construction does.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import pyarrow as pa
import pyarrow.compute as pc
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar.frame import _arrow_unit_to_cell, _unit_type

__all__ = [
    "UnitCodes",
    "array_kernel",
    "call_kernel",
    "collation_keys",
    "collation_sort_keys",
    "map_units",
    "scalar_kernel",
    "unit_cells",
    "unit_codes",
]

_UNIT_TYPE = _unit_type()
_SAME_WIDTH_INT = {16: pa.int16(), 32: pa.int32(), 64: pa.int64()}


def call_kernel(name: str, *args: object, **kwargs: object) -> object:
    """Call the `pyarrow.compute` function `name`, including those PyArrow's
    type stubs omit, and return its result untyped."""
    return cast("Callable[..., object]", getattr(pc, name))(*args, **kwargs)


def array_kernel(name: str, *args: object, **kwargs: object) -> pa.Array:
    """`call_kernel` for a function that returns one `pa.Array`.

    Raises:
        AssertionError: the function returned anything else, such as a
            `pa.ChunkedArray` for a chunked argument.
    """
    result = call_kernel(name, *args, **kwargs)
    assert isinstance(result, pa.Array)
    return result


def scalar_kernel(name: str, *args: object, **kwargs: object) -> pa.Scalar:
    """`call_kernel` for a function that returns one `pa.Scalar`."""
    result = call_kernel(name, *args, **kwargs)
    assert isinstance(result, pa.Scalar)
    return result


def _combined(values: pa.Array | pa.ChunkedArray) -> pa.Array:
    return values.combine_chunks() if isinstance(values, pa.ChunkedArray) else values


@dataclass(frozen=True, slots=True)
class UnitCodes:
    """One dense integer code per row: rows that share a code hold
    identical values.

    Attributes:
        codes: `int64`, one per row. Codes run from 0 to `count - 1` in order
            of each value's first occurrence.
        first_rows: `int64`, one per code: the row index of that code's
            first occurrence, ascending.
    """

    codes: pa.Array
    first_rows: pa.Array

    @property
    def count(self) -> int:
        """The number of distinct values."""
        return len(self.first_rows)


def _dense(key: pa.Array) -> pa.Array:
    encoded = array_kernel("dictionary_encode", key, null_encoding="encode")
    assert isinstance(encoded, pa.DictionaryArray)
    return array_kernel("cast", encoded.indices, pa.int64())


def _leaf_codes(values: pa.Array) -> pa.Array:
    """Dense codes for one non-struct array, null its own code.

    Floats are coded by bit pattern, so `-0.0` and `0.0` stay distinct as
    they are in `to_pylist`. A type outside the hashed set gets one code per
    row: never merging two rows keeps the codes exact at the cost of
    deduplication.
    """
    data_type = values.type
    if pa.types.is_floating(data_type):
        return _dense(values.view(_SAME_WIDTH_INT[data_type.bit_width]))
    if (
        pa.types.is_null(data_type)
        or pa.types.is_boolean(data_type)
        or pa.types.is_integer(data_type)
        or pa.types.is_string(data_type)
        or pa.types.is_large_string(data_type)
        or pa.types.is_binary(data_type)
        or pa.types.is_large_binary(data_type)
    ):
        return _dense(values)
    return pa.array(range(len(values)), type=pa.int64())


def _combine_codes(left: pa.Array, right: pa.Array) -> pa.Array:
    """Dense codes for the pair of two dense code arrays.

    Re-encoding after each pair keeps every intermediate key below
    ``rows * (rows + 1)``, so any number of leaves combines without int64
    overflow.
    """
    if not len(right):
        return right
    width = cast(int, scalar_kernel("max", right).as_py()) + 1
    key = array_kernel(
        "add", array_kernel("multiply", left, pa.scalar(width, pa.int64())), right
    )
    return _dense(key)


def _row_codes(values: pa.Array) -> pa.Array:
    if isinstance(values, pa.DictionaryArray):
        # `take` decodes any value type; Arrow has no cast from a
        # dictionary of structs or lists to its value type.
        return _row_codes(array_kernel("take", values.dictionary, values.indices))
    if not pa.types.is_struct(values.type):
        return _leaf_codes(values)
    assert isinstance(values, pa.StructArray)
    # `flatten` carries the struct's own nulls into each child, and the
    # validity code separates a null struct from one whose children are
    # all null.
    codes = _dense(array_kernel("is_valid", values))
    for child in values.flatten():
        codes = _combine_codes(codes, _row_codes(child))
    return codes


def unit_codes(unit: pa.Array | pa.ChunkedArray) -> UnitCodes:
    """Code each row of `unit` by value, reading the struct's leaf columns.

    PyArrow cannot `dictionary_encode`, `unique`, or `group_by` a struct
    column, so this combines the dictionary codes of every leaf column, and
    each struct level's validity, into one key per row. Two rows share a
    code exactly when every leaf holds the same value and the same nulls.
    Float leaves compare by bit pattern: `-0.0` and `0.0` get separate
    codes, and NaNs with the same bits share one. A leaf of a type outside
    the hashed set (null, boolean, integer, floating, string, binary) gets
    a fresh code on every row, which keeps the codes exact at the cost of
    deduplication. The canonical `unit` struct has only string and integer
    leaves.

    Accepts any Arrow array. Engines use the codes to test unit membership
    and heterogeneity without reading rows: `count` distinct units, and
    `unit_cells` for their values.
    """
    values = _combined(unit)
    codes = _row_codes(values)
    grouped = (
        pa.table({"code": codes, "row": pa.array(range(len(codes)), pa.int64())})
        .group_by("code", use_threads=False)
        .aggregate([("row", "min")])
        .sort_by("code")
    )
    return UnitCodes(codes=codes, first_rows=grouped.column("row_min").combine_chunks())


def map_units(
    unit: pa.Array | pa.ChunkedArray,
    function: Callable[[object], object],
    *,
    result_type: pa.DataType,
) -> pa.Array:
    """Apply `function` once per distinct value of `unit` and take the
    results back to one per row.

    `function` receives each distinct value as `to_pylist` reads it, in
    order of first occurrence, so the first value it refuses belongs to the
    earliest refused row.
    """
    values = _combined(unit)
    codes = unit_codes(values)
    distinct = array_kernel("take", values, codes.first_rows).to_pylist()
    mapped = pa.array([function(value) for value in distinct], type=result_type)
    return array_kernel("take", mapped, codes.codes)


def unit_cells(
    unit: pa.Array | pa.ChunkedArray, codes: UnitCodes
) -> list[str | grammar.UnitRequest | None]:
    """The distinct cells of a canonical `unit` column, indexed by code.

    Each cell has the form `Frame.rows` gives it: a ref string, a
    `grammar.UnitRequest`, or `None`.
    """
    distinct = array_kernel("take", _combined(unit), codes.first_rows).to_pylist()
    return [
        cast("str | grammar.UnitRequest | None", _arrow_unit_to_cell(value))
        for value in distinct
    ]


def _unit_payload_of(value: object) -> bytes:
    if value is None:
        return b""
    tagged = cast("Mapping[str, object]", value)
    ref = tagged["ref"]
    if ref is not None:
        return cast(str, ref).encode("utf-8")
    request = cast("Mapping[str, object]", tagged["request"])
    return canonicalize(
        {key: item for key, item in request.items() if item is not None}
    )


def _unit_payload(unit: pa.Array) -> pa.Array:
    """Binary payload per row: UTF-8 of the ref, JCS of the request, or
    empty for a null unit. Within one rank, byte order is the grammar's
    code-point order."""
    refs = array_kernel("struct_field", unit, "ref")
    has_request = array_kernel(
        "and_kleene",
        array_kernel("is_valid", unit),
        array_kernel("is_null", refs),
    )
    if not scalar_kernel("any", has_request).as_py():
        return array_kernel("fill_null", array_kernel("cast", refs, pa.binary()), b"")
    return map_units(unit, _unit_payload_of, result_type=pa.binary())


def _rank(*conditions: pa.Array) -> pa.Array:
    """`int8` rank: the index of the first true condition, else their count."""
    rank: pa.Array | pa.Scalar = pa.scalar(len(conditions), pa.int8())
    for position in reversed(range(len(conditions))):
        rank = array_kernel(
            "if_else", conditions[position], pa.scalar(position, pa.int8()), rank
        )
    assert isinstance(rank, pa.Array)
    return rank


def collation_keys(values: pa.Array | pa.ChunkedArray) -> tuple[pa.Array, pa.Array]:
    """Rank and payload arrays whose ascending lexicographic order is the
    grammar's `cell_sort_key` order over `values`.

    A string column ranks null (0) before any string (1), with the payload
    the string itself or `""` for null; Arrow compares strings by UTF-8
    bytes, which is code-point order. A canonical `unit` column ranks null
    (0), then a ref (1), then a unit request (2), with the payload its
    UTF-8 ref or JCS request bytes. Two rows are equal under the grammar
    exactly when both arrays are equal.

    Raises:
        TypeError: `values` is neither a string column nor the canonical
            `unit` struct.
    """
    array = _combined(values)
    if pa.types.is_string(array.type):
        is_null = array_kernel("is_null", array)
        return _rank(is_null), array_kernel("fill_null", array, "")
    if array.type == _UNIT_TYPE:
        is_null = array_kernel("is_null", array)
        has_ref = array_kernel("is_valid", array_kernel("struct_field", array, "ref"))
        return _rank(is_null, has_ref), _unit_payload(array)
    raise TypeError(f"no grammar collation for arrow type {array.type!r}")


def collation_sort_keys(table: pa.Table, columns: Sequence[str]) -> pa.Table:
    """A table of `collation_keys` for each of `columns`, in order.

    Sorting ascending by every column of the result, in order, orders rows
    as `grammar.row_sort_key` does when `columns` is the grain (every column
    except `value` and `status`). To reverse one column, sort both of its
    arrays descending. The result has as many rows as `table`, and no
    columns when `columns` is empty.
    """
    keys = table.select([])
    for position, column in enumerate(columns):
        rank, payload = collation_keys(table.column(column))
        keys = keys.append_column(f"__collate_{position}_rank", rank)
        keys = keys.append_column(f"__collate_{position}_payload", payload)
    return keys
