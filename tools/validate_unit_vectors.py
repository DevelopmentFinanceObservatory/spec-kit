# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate every vector under `vectors/units/` against
`schemas/vectors/unit-vector.schema.json`.

Usage:
    uv run tools/validate_unit_vectors.py

Beyond schema conformance, this checks what the schema cannot express: each
file name matches its record's `name`, names are unique, the suite meets its
floor, every classification and every compare outcome has a vector, and
every typed-unit kind appears in some vector's `expected`. It does not import
`dfo_spec_kit`. `python/spec-kit/tests/test_units.py` runs the
implementation against the same files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
VECTORS_DIR = REPO_ROOT / "vectors" / "units"
SCHEMA_PATH = REPO_ROOT / "schemas" / "vectors" / "unit-vector.schema.json"

_MIN_VECTOR_FLOOR = 30


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )


def _missing(label: str, vocabulary: list[str], seen: set[str]) -> list[str]:
    return [f"no vector for {label} {value!r}" for value in vocabulary if value not in seen]


def validate() -> list[str]:
    schema = _load(SCHEMA_PATH)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    paths = sorted(VECTORS_DIR.glob("*.json"))
    problems: list[str] = []
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            record = _load(path)
        except ValueError as exc:
            problems.append(f"{path.name}: not valid JSON: {exc}")
            continue
        for error in sorted(
            validator.iter_errors(record), key=lambda e: list(e.absolute_path)
        ):
            where = "/".join(str(part) for part in error.absolute_path) or "<root>"
            problems.append(f"{path.name}: schema: {where}: {error.message}")
        if isinstance(record, dict):
            records.append(record)
            if record.get("name") != path.stem:
                problems.append(
                    f"{path.name}: name {record.get('name')!r} does not match "
                    "the file name"
                )

    # The checks below read fields the schema may already have rejected, so
    # each reads only values of the type the schema requires and leaves the
    # rest to the schema diagnostics above.
    names = [r["name"] for r in records if isinstance(r.get("name"), str)]
    problems.extend(
        f"duplicate name {n!r}" for n in sorted({n for n in names if names.count(n) > 1})
    )
    if len(paths) < _MIN_VECTOR_FLOOR:
        problems.append(
            f"expected at least {_MIN_VECTOR_FLOOR} vectors, found {len(paths)}"
        )

    problems += _missing(
        "classification",
        schema["properties"]["classification"]["enum"],
        {r["classification"] for r in records if isinstance(r.get("classification"), str)},
    )
    compare = next(
        branch
        for branch in schema["oneOf"]
        if branch["properties"]["classification"].get("const") == "compare"
    )
    problems += _missing(
        "compare outcome",
        compare["properties"]["outcome"]["enum"],
        {r["outcome"] for r in records if isinstance(r.get("outcome"), str)},
    )
    problems += _missing(
        "unit kind",
        [form["properties"]["kind"]["const"] for form in schema["$defs"]["unit"]["oneOf"]],
        {
            r["expected"]["kind"]
            for r in records
            if isinstance(r.get("expected"), dict)
            and isinstance(r["expected"].get("kind"), str)
        },
    )
    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s) in {VECTORS_DIR}", file=sys.stderr)
        return 1
    print(f"OK: {len(list(VECTORS_DIR.glob('*.json')))} unit vectors validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
