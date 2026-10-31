"""The canonical Arrow form of the `unit` cell and its conversions to and
from `grammar.UnitCell`.

`frame`, `columnar` and `arrow` all read and write the `unit` column through
these functions, and this module imports none of them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

import pyarrow as pa
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar.grammar import FrameError


def _unit_request_type(*, recipe: bool) -> pa.StructType:
    fields = [
        pa.field("currency", pa.string()),
        pa.field("prices", pa.string()),
        pa.field("base_year", pa.int64()),
    ]
    if recipe:
        fields.append(pa.field("recipe", pa.string()))
    return pa.struct(fields)


def _unit_type(*, recipe: bool) -> pa.StructType:
    """The one nullable tagged representation of the unit union: exactly
    one member is non-null in a non-null cell. `composed` holds the JCS text
    of a composed unit's JSON form.

    `request` carries a `recipe` child only in the recipe layout, which a
    column takes when some cell carries a recipe. Without one the column is
    the layout frame grammar `1.0.0` reads, so a frame with no recipe stores
    as it did under that release.
    """
    return pa.struct(
        [
            pa.field("ref", pa.string()),
            pa.field("request", _unit_request_type(recipe=recipe)),
            pa.field("composed", pa.string()),
        ]
    )


# Every layout of the `unit` struct a release stored: the two-member
# `struct<ref, request>`, which holds no composed unit, and the three-member
# struct with and without the `recipe` child of `request`.
_TWO_MEMBER_UNIT_TYPE = pa.struct(
    [
        pa.field("ref", pa.string()),
        pa.field("request", _unit_request_type(recipe=False)),
    ]
)
_STORED_UNIT_TYPES = (
    _TWO_MEMBER_UNIT_TYPE,
    _unit_type(recipe=False),
    _unit_type(recipe=True),
)


def _holds_recipe(unit: pa.Array | pa.ChunkedArray) -> bool:
    """Whether some cell of a `unit` column in one of `_STORED_UNIT_TYPES`
    carries a recipe. Reads validity bitmaps only."""
    if unit.type != _unit_type(recipe=True):
        return False
    chunks = unit.chunks if isinstance(unit, pa.ChunkedArray) else [unit]
    for chunk in chunks:
        # `flatten` folds each parent's validity into its children, so a
        # recipe under a null request or a null unit does not count.
        request = cast(pa.StructArray, chunk).flatten()[1]
        recipe = cast(pa.StructArray, request).flatten()[3]
        if recipe.null_count < len(recipe):
            return True
    return False


def _conform_unit(unit: pa.Array, *, recipe: bool) -> pa.Array:
    """`unit`, a column in one of `_STORED_UNIT_TYPES`, in the recipe layout
    when `recipe` is true or some cell carries a recipe, and in the layout
    without the `recipe` child otherwise. Every cell is unchanged."""
    target = _unit_type(recipe=recipe or _holds_recipe(unit))
    if unit.type == target:
        return unit
    struct = cast(pa.StructArray, unit)
    request = cast(pa.StructArray, struct.field("request"))
    request_type = cast(pa.StructType, target.field("request").type)
    widened_request = pa.StructArray.from_arrays(
        [
            request.field(field.name)
            if field.name in request.type.names
            else pa.nulls(len(request), type=field.type)
            for field in request_type
        ],
        fields=list(request_type),
        mask=request.is_null(),
    )
    return pa.StructArray.from_arrays(
        [
            struct.field("ref"),
            widened_request,
            struct.field("composed")
            if "composed" in struct.type.names
            else pa.nulls(len(struct), type=pa.string()),
        ],
        fields=list(target),
        mask=struct.is_null(),
    )


def _unit_array(cells: list[dict[str, object] | None]) -> pa.Array:
    """Tagged struct values, as `_unit_to_arrow` gives them, as a `unit`
    column in the layout its cells need."""
    return _conform_unit(pa.array(cells, _unit_type(recipe=True)), recipe=False)


def _unit_to_arrow(cell: grammar.UnitCell | None) -> dict[str, object] | None:
    """`cell` as the tagged struct value `pa.array` reads; a member left out
    is null."""
    if cell is None:
        return None
    if isinstance(cell, str):
        return {"ref": cell}
    if isinstance(cell, grammar.UnitRequest):
        return {"request": cell.as_json()}
    return {"composed": canonicalize(cell.as_json()).decode("utf-8")}


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
    is a null unit. `unit_cells`, `Frame.rows` and the Arrow build read a
    `unit` column through it, so each refuses a stored cell outside these
    forms.

    Raises:
        FrameError: `schema_violation` for a struct with no member or more
            than one, or a member of the wrong type; `invalid_unit` for a
            member outside its form.
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
        if not isinstance(value, str):
            raise _wrong_unit_type(value)
        return _composed_unit_from_text(value)
    if not isinstance(value, str):
        raise _wrong_unit_type(value)
    return grammar.unit_cell_from_json(value)


def _arrow_part_to_cell(value: object) -> grammar.UnitPart:
    """A ref string or a `request` struct as its validated cell, refusing
    what `grammar.unit_cell_from_json` would refuse."""
    if isinstance(value, Mapping):
        return grammar.unit_request_from_json(_without_nulls(value))
    return _unit_ref(value)


def _unit_ref(value: object) -> str:
    cell = grammar.unit_cell_from_json(value)
    if not isinstance(cell, str):
        raise _wrong_unit_type(value)
    return cell
