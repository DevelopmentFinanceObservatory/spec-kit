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
entries are sorted by (`code`, `reason`), each (`code`, `reason`) pair appears
once, every `code` is an active warning in registries/codes.json, and the
packaged mirror (`python/spec-kit/src/dfo_spec_kit/registry/warning-reasons.json`)
is byte-identical to the canonical file. It does not import `dfo_spec_kit`.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
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
    counts = Counter(key for key, _ in pairs)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
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


def check_entries(registry: dict[str, Any], codes: dict[str, Any]) -> list[str]:
    """The rules a schema-valid registry must also meet. `codes` is the parsed
    registries/codes.json."""
    keys = [(entry["code"], entry["reason"]) for entry in registry["entries"]]
    problems: list[str] = []
    if keys != sorted(keys):
        problems.append("entries is not sorted by code and reason")
    counts = Counter(keys)
    problems.extend(
        f"reason declared twice for {code!r}: {reason!r}"
        for (code, reason), count in sorted(counts.items())
        if count > 1
    )
    active_warnings = {
        entry["code"]
        for entry in codes["entries"]
        if entry["classification"] == "warning" and entry["lifecycle"] == "active"
    }
    problems.extend(
        f"code is not an active warning in registries/codes.json: {code!r}"
        for code in sorted({code for code, _ in keys} - active_warnings)
    )
    return problems


def check_mirror() -> list[str]:
    if MIRROR_PATH.read_bytes() != REGISTRY_PATH.read_bytes():
        mirror = MIRROR_PATH.relative_to(REPO_ROOT)
        canonical = REGISTRY_PATH.relative_to(REPO_ROOT)
        return [f"{mirror} is not byte-identical to {canonical}"]
    return []


def validate() -> tuple[list[str], int]:
    """The problems found and the number of entries checked."""
    try:
        schema = load_json(SCHEMA_PATH)
        registry = load_json(REGISTRY_PATH)
        codes = load_json(CODES_PATH)
    except ValueError as exc:
        return [f"not valid JSON: {exc}"], 0
    problems = check_schema(registry, schema)
    if problems:
        return problems, 0
    problems = check_entries(registry, codes) + check_mirror()
    return problems, len(registry["entries"])


def main() -> int:
    problems, count = validate()
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK: {count} warning reasons validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
