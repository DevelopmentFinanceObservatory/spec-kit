"""Build canonical Arrow-backed frames from Arrow tables and CSV data."""

from __future__ import annotations

import io
import json
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

import pyarrow as pa
import pyarrow.csv as pacsv

from dfo_frame_grammar import grammar
from dfo_frame_grammar._unit_codec import (
    _conform_unit,
    _unit_to_arrow,
    _unit_type,
)
from dfo_frame_grammar.columnar import (
    _sort_and_check,
    array_kernel,
    map_units,
    scalar_kernel,
)
from dfo_frame_grammar.frame import Frame, canonical_frame_schema
from dfo_frame_grammar.grammar import FrameError

__all__ = ["build_frame_from_arrow", "read_frame_csv"]


def _normalize_schema_names(table: pa.Table) -> dict[str, str]:
    """Return NFC-normalised source names, refusing collisions."""
    raw_by_norm: dict[str, str] = {}
    for raw_name in table.schema.names:
        norm = (
            raw_name
            if unicodedata.is_normalized("NFC", raw_name)
            else unicodedata.normalize("NFC", raw_name)
        )
        if norm in raw_by_norm and raw_by_norm[norm] != raw_name:
            raise FrameError(
                "schema_violation",
                f"column names {raw_name!r} and {raw_by_norm[norm]!r} "
                "collide after NFC normalisation",
            )
        if norm in raw_by_norm:
            raise FrameError("schema_violation", f"duplicate column name {raw_name!r}")
        raw_by_norm[norm] = raw_name
    return raw_by_norm


def _combined(array: pa.ChunkedArray) -> pa.Array:
    return array.combine_chunks()


def _is_text_type(data_type: pa.DataType) -> bool:
    if pa.types.is_dictionary(data_type):
        return _is_text_type(data_type.value_type)
    return (
        pa.types.is_string(data_type)
        or pa.types.is_large_string(data_type)
        or pa.types.is_string_view(data_type)
    )


def _any(value: pa.Array | pa.ChunkedArray) -> bool:
    return bool(scalar_kernel("any", value).as_py())


def _normalise_text(
    array: pa.ChunkedArray,
    *,
    description: str,
    allow_null: bool,
    validate: Callable[[str], None] | None = None,
) -> pa.Array:
    """Validate distinct text values once, then take them through Arrow."""
    if pa.types.is_null(array.type):
        if allow_null:
            return pa.nulls(len(array), type=pa.string())
        if not len(array):
            return pa.array([], type=pa.string())
        raise FrameError(
            "schema_violation", f"{description} must be a string, got {type(None)!r}"
        )
    if not _is_text_type(array.type):
        raise FrameError(
            "schema_violation",
            f"{description} must be a string, got arrow type {array.type!r}",
        )
    encoded = array_kernel("dictionary_encode", _combined(array))
    assert isinstance(encoded, pa.DictionaryArray)
    values: list[str | None] = []
    for raw in encoded.dictionary.to_pylist():
        if raw is None:
            values.append(None)
            continue
        assert isinstance(raw, str)
        text = (
            raw
            if unicodedata.is_normalized("NFC", raw)
            else unicodedata.normalize("NFC", raw)
        )
        grammar.reject_lone_surrogates(text)
        if not text:
            raise FrameError(
                "schema_violation", f"{description} must be a non-empty string"
            )
        if validate is not None:
            validate(text)
        values.append(text)
    normalised = array_kernel(
        "take", pa.array(values, type=pa.string()), encoded.indices
    )
    if not allow_null and _any(array_kernel("is_null", normalised)):
        raise FrameError(
            "schema_violation", f"{description} must be a string, got {type(None)!r}"
        )
    return normalised


def _normalise_time_period(array: pa.ChunkedArray) -> pa.Array:
    return _normalise_text(
        array,
        description="time_period",
        allow_null=False,
        validate=grammar.validate_time_period,
    )


def _normalise_value(array: pa.ChunkedArray) -> pa.Array:
    if not len(array):
        return pa.array([], type=pa.float64())
    data_type = array.type
    if pa.types.is_boolean(data_type) or not (
        pa.types.is_integer(data_type) or pa.types.is_floating(data_type)
    ):
        raise FrameError(
            "schema_violation",
            f"value column must be int or float, got arrow type {data_type!r}",
        )
    values = _combined(array)
    if _any(array_kernel("is_null", values)):
        raise FrameError(
            "schema_violation", f"value must be an int or float, got {type(None)!r}"
        )
    try:
        result = array_kernel("cast", values, pa.float64(), safe=False)
    except pa.ArrowInvalid as exc:
        raise FrameError(
            "non_finite_number", "value overflows the finite double domain"
        ) from exc
    if _any(array_kernel("invert", array_kernel("is_finite", result))):
        raise FrameError("non_finite_number", "value must be a finite number")
    return array_kernel(
        "if_else", array_kernel("equal", result, 0.0), pa.scalar(0.0), result
    )


def _without_nulls(struct: Mapping[Any, object]) -> dict[str, object]:
    """The non-null members of a struct value as `to_pylist` reads it."""
    return {key: value for key, value in struct.items() if value is not None}


def _wrong_unit_type(value: object) -> FrameError:
    return FrameError(
        "schema_violation",
        f"unit must be a string, mapping, or null, got {type(value)!r}",
    )


def _unit_request_from_mapping(raw: object) -> grammar.UnitRequest | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _wrong_unit_type(raw)
    return grammar.unit_request_from_json(_without_nulls(raw))


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    members = dict(pairs)
    if len(members) != len(pairs):
        raise FrameError("invalid_unit", "composed unit text repeats a member name")
    return members


def _composed_unit_from_text(text: str) -> grammar.ComposedUnit:
    """Parse and validate the JSON text of a composed unit, refusing text
    that is not JSON, repeats a member name, or holds anything but a
    composed unit."""
    try:
        parsed = json.loads(text, object_pairs_hook=_unique_members)
    # `ValueError` covers `JSONDecodeError` and an integer past Python's digit
    # limit; `RecursionError` covers nesting past the interpreter's depth.
    except (ValueError, RecursionError) as exc:
        raise FrameError(
            "invalid_unit", f"composed unit text is not JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise FrameError(
            "invalid_unit",
            f"composed unit text must hold a JSON object, got {type(parsed)!r}",
        )
    return grammar._composed_unit_from_json(parsed)


def _canonical_unit_from_mapping(raw: object) -> grammar.UnitCell | None:
    """The validated cell of one tagged struct value, read from its single
    non-null member: a `ref` string, a `request` struct with its null
    members dropped, or the JSON text of a `composed` unit. A null struct
    is a null unit. `_refuse_untyped_composed` has already refused a
    `composed` member that is not text.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise _wrong_unit_type(raw)
    variants = _without_nulls(raw)
    if len(variants) != 1:
        raise FrameError(
            "schema_violation", "unit tagged struct must carry one variant"
        )
    ((member, value),) = variants.items()
    if member == "request":
        return _unit_request_from_mapping(value)
    if member == "composed":
        return _composed_unit_from_text(cast(str, value))
    if not isinstance(value, str):
        raise _wrong_unit_type(value)
    return grammar.unit_cell_from_json(value)


# The tagged spellings of the `unit` struct: the canonical three-member
# layout and the two-member layout, which holds no composed unit. A struct
# with any other member names is a bare unit request.
_TAGGED_UNIT_MEMBERS = (["ref", "request"], ["ref", "request", "composed"])


def _refuse_untyped_composed(data_type: pa.StructType) -> None:
    """Refuse a three-member tagged struct whose `composed` member is not
    text by its Arrow type, so the column refuses whatever rows it holds.
    A null-typed member holds no composed unit and passes."""
    if data_type.names != _TAGGED_UNIT_MEMBERS[1]:
        return
    composed = data_type.field("composed").type
    if not (_is_text_type(composed) or pa.types.is_null(composed)):
        raise FrameError(
            "schema_violation",
            f"unit composed member must be a string, got arrow type {composed!r}",
        )


def _normalise_unit(array: pa.ChunkedArray) -> pa.Array:
    """The `unit` column in the layout its cells need: with the `recipe`
    child of `request` only when some cell carries a recipe."""
    unit_type = _unit_type(recipe=False)
    if pa.types.is_null(array.type):
        return pa.nulls(len(array), type=unit_type)
    if _is_text_type(array.type):
        refs = _normalise_text(array, description="unit", allow_null=True)
        return pa.StructArray.from_arrays(
            [
                refs,
                *(
                    pa.nulls(len(refs), type=unit_type.field(member).type)
                    for member in ("request", "composed")
                ),
            ],
            fields=list(unit_type),
            mask=array_kernel("is_null", refs),
        )
    if not pa.types.is_struct(array.type):
        raise FrameError(
            "schema_violation",
            f"unit must be a string, mapping, or null, got arrow type {array.type!r}",
        )
    _refuse_untyped_composed(array.type)
    normalise = (
        _canonical_unit_from_mapping
        if array.type.names in _TAGGED_UNIT_MEMBERS
        else _unit_request_from_mapping
    )
    units = map_units(
        array,
        lambda value: _unit_to_arrow(normalise(value)),
        result_type=_unit_type(recipe=True),
    )
    return _conform_unit(units, recipe=False)


def build_frame_from_arrow(table: pa.Table, *, role_keys: Iterable[str]) -> Frame:
    """Validate a table directly into the canonical physical frame table."""
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
    raw_by_norm = _normalize_schema_names(table)
    if grammar.STATUS_COLUMN in raw_by_norm:
        raise FrameError(
            "schema_violation", "status is reserved and may not be supplied"
        )
    unknown = set(raw_by_norm) - allowed
    if unknown:
        raise FrameError(
            "schema_violation", f"table carries unknown column(s): {sorted(unknown)!r}"
        )
    missing = required - set(raw_by_norm)
    if missing:
        raise FrameError(
            "schema_violation",
            f"table is missing required column(s): {sorted(missing)!r}",
        )

    arrays: dict[str, pa.Array] = {}
    for column in (grammar.STAT_VAR_COLUMN, *role_columns):
        arrays[column] = _normalise_text(
            table.column(raw_by_norm[column]),
            description="stat_var"
            if column == grammar.STAT_VAR_COLUMN
            else f"role {column!r}",
            allow_null=False,
            validate=grammar.validate_stat_var_ref
            if column == grammar.STAT_VAR_COLUMN
            else None,
        )
    arrays[grammar.TIME_PERIOD_COLUMN] = _normalise_time_period(
        table.column(raw_by_norm[grammar.TIME_PERIOD_COLUMN])
    )
    arrays[grammar.VALUE_COLUMN] = _normalise_value(
        table.column(raw_by_norm[grammar.VALUE_COLUMN])
    )
    arrays[grammar.UNIT_COLUMN] = (
        _normalise_unit(table.column(raw_by_norm[grammar.UNIT_COLUMN]))
        if grammar.UNIT_COLUMN in raw_by_norm
        else pa.nulls(table.num_rows, type=_unit_type(recipe=False))
    )
    for axis in grammar.FACET_IDENTITY_COLUMNS:
        arrays[axis] = (
            _normalise_text(
                table.column(raw_by_norm[axis]),
                description=f"facet {axis!r}",
                allow_null=True,
            )
            if axis in raw_by_norm
            else pa.nulls(table.num_rows, type=pa.string())
        )
    arrays[grammar.STATUS_COLUMN] = pa.nulls(table.num_rows)
    recipe = arrays[grammar.UNIT_COLUMN].type == _unit_type(recipe=True)
    canonical = pa.Table.from_arrays(
        [arrays[column] for column in columns],
        schema=canonical_frame_schema(columns, recipe=recipe),
    )
    return Frame._from_validated(
        columns=columns, table=_sort_and_check(canonical, columns=columns)
    )


def read_frame_csv(csv_bytes: bytes, *, role_keys: Iterable[str]) -> pa.Table:
    """Read grammar-shaped CSV data into a table for frame construction."""
    role_columns = grammar.normalize_role_keys(tuple(role_keys))
    string_columns = (
        grammar.STAT_VAR_COLUMN,
        *role_columns,
        grammar.TIME_PERIOD_COLUMN,
        grammar.UNIT_COLUMN,
        *grammar.FACET_IDENTITY_COLUMNS,
    )
    column_types = {name: pa.string() for name in string_columns}
    column_types[grammar.VALUE_COLUMN] = pa.float64()
    return pacsv.read_csv(
        io.BytesIO(csv_bytes),
        convert_options=pacsv.ConvertOptions(
            column_types=column_types, strings_can_be_null=True, null_values=[""]
        ),
    )
