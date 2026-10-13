"""The canonical Arrow form of the `unit` cell and its conversions to and
from `grammar.UnitCell`.

`frame`, `columnar` and `arrow` all read and write the `unit` column through
these functions, and this module imports none of them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import pyarrow as pa
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar


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


def _arrow_part_to_cell(value: object) -> grammar.UnitPart:
    """A ref string, a `request` struct, or one side of a parsed composed
    unit as its cell, trusted as canonical."""
    if isinstance(value, str):
        return value
    part = cast("Mapping[str, object]", value)
    if grammar.STAT_VAR_COLUMN in part:
        return grammar.StatVarPart(stat_var=cast(str, part[grammar.STAT_VAR_COLUMN]))
    return grammar.UnitRequest(
        currency=cast(str, part["currency"]),
        prices=cast(str, part["prices"]),
        base_year=cast("int | None", part.get("base_year")),
        recipe=cast("str | None", part.get("recipe")),
    )


def _arrow_unit_to_cell(value: object) -> grammar.UnitCell | None:
    if value is None:
        return None
    assert isinstance(value, Mapping)
    tagged = cast("Mapping[str, object]", value)
    composed = tagged["composed"]
    if composed is None:
        member = tagged["ref"] if tagged["ref"] is not None else tagged["request"]
        return cast("str | grammar.UnitRequest", _arrow_part_to_cell(member))
    sides = json.loads(cast(str, composed))
    return grammar.ComposedUnit(
        numerator=_arrow_part_to_cell(sides["numerator"]),
        denominator=_arrow_part_to_cell(sides["denominator"]),
    )
