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


def _unit_request_type() -> pa.StructType:
    return pa.struct(
        [
            pa.field("currency", pa.string()),
            pa.field("prices", pa.string()),
            pa.field("base_year", pa.int64()),
        ]
    )


def _unit_type() -> pa.StructType:
    """The one nullable tagged representation of the unit union: exactly
    one member is non-null in a non-null cell. `composed` holds the JCS text
    of a composed unit's JSON form."""
    return pa.struct(
        [
            pa.field("ref", pa.string()),
            pa.field("request", _unit_request_type()),
            pa.field("composed", pa.string()),
        ]
    )


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
