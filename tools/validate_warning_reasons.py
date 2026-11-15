# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate registries/warning-reasons.json against
schemas/registry/warning-reason-registry.schema.json.

Run with: uv run tools/validate_warning_reasons.py

Beyond schema conformance, this checks what the schema cannot express:
entries are sorted by (`code`, `reason`), each `reason` appears once within
its `code`, every `code` is registered in registries/codes.json, and the
packaged mirror (`python/spec-kit/src/dfo_spec_kit/registry/warning-reasons.json`)
is byte-identical to the canonical file. It does not import `dfo_spec_kit`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry" / "warning-reason-registry.schema.json"
REGISTRY_PATH = REPO_ROOT / "registries" / "warning-reasons.json"
CODES_PATH = REPO_ROOT / "registries" / "codes.json"
MIRROR_PATH = (
    REPO_ROOT / "python/spec-kit/src/dfo_spec_kit/registry/warning-reasons.json"
)


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


def check_entries(registry: dict[str, Any], codes: set[str]) -> list[str]:
    """The rules a schema-valid registry must also meet."""
    keys = [(entry["code"], entry["reason"]) for entry in registry["entries"]]
    problems: list[str] = []
    if keys != sorted(keys):
        problems.append("entries is not sorted by code and reason")
    problems.extend(
        f"reason declared twice for {code!r}: {reason!r}"
        for code, reason in sorted({key for key in keys if keys.count(key) > 1})
    )
    problems.extend(
        f"code is not in registries/codes.json: {code!r}"
        for code in sorted({code for code, _ in keys} - codes)
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
        codes = {entry["code"] for entry in load_json(CODES_PATH)["entries"]}
    except ValueError as exc:
        return [f"not valid JSON: {exc}"]
    problems = check_schema(registry, schema)
    if problems:
        return problems
    return check_entries(registry, codes) + check_mirror()


def main() -> int:
    problems = validate()
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    count = len(load_json(REGISTRY_PATH)["entries"])
    print(f"OK: {count} warning reasons validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
