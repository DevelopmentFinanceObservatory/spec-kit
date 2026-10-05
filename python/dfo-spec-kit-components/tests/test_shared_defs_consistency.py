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
