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

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry" / "unit-registry.schema.json"
REGISTRY_PATH = REPO_ROOT / "registries" / "units.json"
MIRROR_PATH = REPO_ROOT / "python/spec-kit/src/dfo_spec_kit/units/units.json"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )


def check_schema(registry: Any, schema: Any) -> list[str]:
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"schema: {'/'.join(str(p) for p in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in sorted(
            validator.iter_errors(registry), key=lambda e: list(e.absolute_path)
        )
    ]


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
    problems.extend(
        f"unit ref declared twice: {ref!r}"
        for ref in sorted({ref for ref in refs if refs.count(ref) > 1})
    )
    code_set = frozenset(codes)
    problems.extend(
        f"unit ref {ref!r} is declared and matches a prefix rule"
        for ref in refs
        if matches_prefix_rule(ref, registry["currency_prefixes"], code_set)
    )
    return problems


def check_mirror() -> list[str]:
    if MIRROR_PATH.read_bytes() != REGISTRY_PATH.read_bytes():
        mirror = MIRROR_PATH.relative_to(REPO_ROOT)
        canonical = REGISTRY_PATH.relative_to(REPO_ROOT)
        return [f"{mirror} is not byte-identical to {canonical}"]
    return []


def validate() -> list[str]:
    try:
        schema = load_json(SCHEMA_PATH)
        registry = load_json(REGISTRY_PATH)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    problems = check_schema(registry, schema)
    if problems:
        return problems
    return check_rows(registry) + check_mirror()


def main() -> int:
    problems = validate()
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK: {len(load_json(REGISTRY_PATH)['units'])} unit registry rows validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
