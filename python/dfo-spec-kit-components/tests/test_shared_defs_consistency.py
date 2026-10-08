"""The six component schemas (`schemas/component/*.schema.json`) each
declare their own `$defs`, and several names recur across more than one
schema (`period`, `structured_unit`, `unit`, ...) because there is no
shared schema to `$ref` into yet. Until that factoring happens, every
copy of a given `$defs` name must describe the same shape -- a schema
that drifted from its siblings would validate payloads inconsistently
depending on which component authored them.
"""

from __future__ import annotations

import json

from dfo_spec_kit_components import bundle_dir

_BUNDLE = bundle_dir()
_COMPONENT_SCHEMA_DIR = _BUNDLE / "schemas" / "component"


def _component_defs() -> dict[str, dict[str, dict]]:
    """Maps each `$defs` name to {component name: parsed definition} for
    every component schema that declares it."""
    by_name: dict[str, dict[str, dict]] = {}
    for entry in sorted(_COMPONENT_SCHEMA_DIR.glob("*.schema.json")):
        schema = json.loads(entry.read_text(encoding="utf-8"))
        component = entry.name.removesuffix(".schema.json")
        for def_name, definition in schema.get("$defs", {}).items():
            by_name.setdefault(def_name, {})[component] = definition
    return by_name


def test_shared_defs_names_exist() -> None:
    """A guard against a refactor that silently stops finding component
    schemas or their `$defs`."""
    shared = {
        name: by_comp for name, by_comp in _component_defs().items() if len(by_comp) > 1
    }
    assert shared, "expected at least one $defs name shared across component schemas"


def test_shared_defs_are_identical_across_component_schemas() -> None:
    mismatches = []
    for def_name, by_component in _component_defs().items():
        if len(by_component) < 2:
            continue
        components = sorted(by_component)
        reference = by_component[components[0]]
        mismatches.extend(
            (def_name, components[0], component)
            for component in components[1:]
            if by_component[component] != reference
        )
    assert not mismatches, (
        f"$defs drifted between component schemas (name, one component, the diverging "
        f"component): {mismatches}"
    )


_UNIT_BEARING = ("bar_chart", "line_chart", "stat_card", "table")
_COMPOSED_UNIT_DEFS = ("composed_unit", "unit_part", "counted_stat_var")


def test_every_unit_bearing_schema_declares_the_composed_unit_defs() -> None:
    """The identity check above compares only the copies that exist, so a
    schema that dropped one of these would pass it."""
    defs = _component_defs()
    for def_name in _COMPOSED_UNIT_DEFS:
        assert sorted(defs[def_name]) == list(_UNIT_BEARING), def_name


def test_the_unit_slot_admits_the_same_forms_in_every_unit_bearing_schema() -> None:
    """`stat_card` states its unit slot inline while the other three keep it
    under `$defs.unit`, so the `$defs` comparison never sees it."""
    slots = {}
    for component in _UNIT_BEARING:
        path = _COMPONENT_SCHEMA_DIR / f"{component}.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        slot = schema["$defs"].get("unit") or schema["properties"]["unit"]
        slots[component] = slot["oneOf"]
    reference = slots["line_chart"]
    assert {"$ref": "#/$defs/composed_unit"} in reference
    assert all(forms == reference for forms in slots.values()), slots
