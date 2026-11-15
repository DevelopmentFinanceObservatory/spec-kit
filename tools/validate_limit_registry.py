# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate registries/limit-names.json against
schemas/registry/limit-name-registry.schema.json.

Run with: uv run tools/validate_limit_registry.py

Beyond schema conformance, this checks what the schema cannot express:
entries are sorted by `name`, each `name` appears once, and the packaged
mirror (`python/spec-kit/src/dfo_spec_kit/registry/limit-names.json`) is
byte-identical to the canonical file. It does not import `dfo_spec_kit`.
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
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry" / "limit-name-registry.schema.json"
REGISTRY_PATH = REPO_ROOT / "registries" / "limit-names.json"
MIRROR_PATH = REPO_ROOT / "python/spec-kit/src/dfo_spec_kit/registry/limit-names.json"


def check_entries(registry: dict[str, Any]) -> list[str]:
    """The rules a schema-valid registry must also meet."""
    names = [entry["name"] for entry in registry["entries"]]
    problems: list[str] = []
    if names != sorted(names):
        problems.append("entries is not sorted by name")
    problems.extend(
        f"limit name declared twice: {name!r}" for name in duplicates(names)
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
    return check_entries(registry) + check_mirror(), registry


def main() -> int:
    problems, registry = validate()
    count = 0 if problems else len(registry["entries"])
    return report(problems, ok=f"{count} limit names validated")


if __name__ == "__main__":
    sys.exit(main())
