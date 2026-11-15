# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate registries/units.json against schemas/registry/unit-registry.schema.json.

Run with: uv run tools/validate_unit_registry.py

Beyond schema conformance (closed rows per kind, member types, the
`format_version` pin), this checks what the schema cannot express: rows are
sorted by `ref` and each `ref` appears once, `currency_codes` is sorted, no
row also matches a currency prefix rule, and the packaged mirror
(`python/spec-kit/src/dfo_spec_kit/units/units.json`) is byte-identical to
the canonical file. It does not import `dfo_spec_kit`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import registry_checks
from registry_checks import (
    RegistryFileError,
    check_schema,
    duplicates,
    load_json,
    report,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry" / "unit-registry.schema.json"
REGISTRY_PATH = REPO_ROOT / "registries" / "units.json"
MIRROR_PATH = REPO_ROOT / "python/spec-kit/src/dfo_spec_kit/units/units.json"


def _is_code_shape(value: str) -> bool:
    return len(value) == 3 and value.isascii() and value.isalpha() and value.isupper()


def matches_prefix_rule(ref: str, prefixes: list[str], codes: frozenset[str]) -> bool:
    """Whether `ref` is a prefix plus a currency code, by the rule
    `dfo_spec_kit.units` applies: after the empty prefix the code must be one
    of `codes`, after any other prefix it need only have the shape."""
    for prefix in prefixes:
        code = ref[len(prefix) :]
        if (
            ref.startswith(prefix)
            and _is_code_shape(code)
            and (prefix or code in codes)
        ):
            return True
    return False


def check_rows(registry: dict[str, Any]) -> list[str]:
    """The rules a schema-valid registry must also meet."""
    problems: list[str] = []
    codes = registry["currency_codes"]
    if codes != sorted(codes):
        problems.append("currency_codes is not sorted")
    refs = [row["ref"] for row in registry["units"]]
    if refs != sorted(refs):
        problems.append("units is not sorted by ref")
    problems.extend(f"unit ref declared twice: {ref!r}" for ref in duplicates(refs))
    code_set = frozenset(codes)
    problems.extend(
        f"unit ref {ref!r} is declared and matches a prefix rule"
        for ref in refs
        if matches_prefix_rule(ref, registry["currency_prefixes"], code_set)
    )
    return problems


def check_mirror() -> list[str]:
    return registry_checks.check_mirror(REGISTRY_PATH, MIRROR_PATH)


def validate() -> tuple[list[str], Any]:
    """The problems found, and the parsed registry (None when unreadable)."""
    try:
        schema = load_json(SCHEMA_PATH)
        registry = load_json(REGISTRY_PATH)
    except RegistryFileError as exc:
        return [str(exc)], None
    problems = check_schema(registry, schema)
    if problems:
        return problems, registry
    return check_rows(registry) + check_mirror(), registry


def main() -> int:
    problems, registry = validate()
    count = 0 if problems else len(registry["units"])
    return report(problems, ok=f"{count} unit registry rows validated")


if __name__ == "__main__":
    sys.exit(main())
