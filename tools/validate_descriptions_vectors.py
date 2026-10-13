# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate the step and description schemas, the operation list, and every
vector under `python/descriptions/src/dfo_descriptions/vectors/`.

Usage:
    uv run tools/validate_descriptions_vectors.py

Checks that the three schemas are valid draft 2020-12, that `operations.json`
validates against `operations.schema.json` and names each op once, the nine
analysis operations exactly, each reason once per op, and roles exactly when
an op takes them. Checks every vector against `descriptions-vector.schema.json`
and its input against the schema it names: an accepted vector and a vector the
structure rules reject must validate, and a vector the schema rejects must not.
Then what the record schema cannot express: each file name matches its `id`,
ids are unique, the suite meets its floor, every structure reason has a
rejection vector, and each of the two schemas has an accepting and a rejecting
vector. It does not import `dfo_descriptions`, so the structure rules
themselves are checked by `python/descriptions/tests/test_vectors.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "python" / "descriptions" / "src" / "dfo_descriptions"
SCHEMAS_DIR = PACKAGE_DIR / "schemas"
VECTORS_DIR = PACKAGE_DIR / "vectors"
SCHEMA_PATH = VECTORS_DIR / "descriptions-vector.schema.json"

_MIN_VECTOR_FLOOR = 40
_SUBJECT_SCHEMAS = {
    "steps": "steps.schema.json",
    "description": "description.schema.json",
}
_ANALYSIS_OPS = {
    "sum",
    "quotient",
    "share",
    "growth",
    "rank",
    "aggregate",
    "filter",
    "compare",
    "join",
}


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


def _validators() -> dict[str, jsonschema.Draft202012Validator]:
    """One validator per packaged schema, sharing a registry so a relative
    `$ref` between them resolves by `$id`."""
    schemas = {
        name: _load(SCHEMAS_DIR / name)
        for name in (*_SUBJECT_SCHEMAS.values(), "operations.schema.json")
    }
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return {
        name: jsonschema.Draft202012Validator(schema, registry=registry)
        for name, schema in schemas.items()
    }


def _check_operations(validator: jsonschema.Draft202012Validator) -> list[str]:
    document = _load(SCHEMAS_DIR / "operations.json")
    problems = [
        f"operations.json: schema: {'/'.join(map(str, e.absolute_path)) or '<root>'}: "
        f"{e.message}"
        for e in validator.iter_errors(document)
    ]
    if problems:
        return problems

    entries = document["operations"]
    ops = [entry["op"] for entry in entries]
    problems.extend(
        f"operations.json: op {op!r} is listed twice"
        for op in sorted({op for op in ops if ops.count(op) > 1})
    )
    analysis = {entry["op"] for entry in entries if entry["kind"] == "analysis"}
    if analysis != _ANALYSIS_OPS:
        problems.append(
            f"operations.json: analysis ops {sorted(analysis)} are not the nine "
            f"{sorted(_ANALYSIS_OPS)}"
        )
    for entry in entries:
        reasons = [item["reason"] for item in entry["left_out"]]
        problems.extend(
            f"operations.json: {entry['op']}: reason {reason!r} is listed twice"
            for reason in sorted({r for r in reasons if reasons.count(r) > 1})
        )
        if (entry["input"] == "roles") != bool(entry["roles"]):
            problems.append(
                f"operations.json: {entry['op']}: roles must be listed exactly when "
                "input is 'roles'"
            )
    return problems


def validate() -> list[str]:
    record_schema = _load(SCHEMA_PATH)
    jsonschema.Draft202012Validator.check_schema(record_schema)
    record_validator = jsonschema.Draft202012Validator(record_schema)
    subjects = _validators()
    problems = _check_operations(subjects["operations.schema.json"])

    paths = sorted(p for p in VECTORS_DIR.glob("*.json") if p != SCHEMA_PATH)
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            record = _load(path)
        except ValueError as exc:
            problems.append(f"{path.name}: not valid JSON: {exc}")
            continue
        record_errors = sorted(
            record_validator.iter_errors(record), key=lambda e: list(e.absolute_path)
        )
        for error in record_errors:
            where = "/".join(str(part) for part in error.absolute_path) or "<root>"
            problems.append(f"{path.name}: schema: {where}: {error.message}")
        if not isinstance(record, dict):
            continue
        records.append(record)
        if record.get("id") != path.stem:
            problems.append(
                f"{path.name}: id {record.get('id')!r} does not match the file name"
            )
        if record_errors:
            continue
        subject = subjects[_SUBJECT_SCHEMAS[record["schema"]]]
        valid = subject.is_valid(record["input"])
        schema_rejects = record.get("error", {}).get("by") == "schema"
        if valid and schema_rejects:
            problems.append(
                f"{path.name}: input validates against {record['schema']} "
                "but the vector expects the schema to reject it"
            )
        elif not valid and not schema_rejects:
            first = next(subject.iter_errors(record["input"]))
            where = "/".join(str(part) for part in first.absolute_path) or "<root>"
            problems.append(
                f"{path.name}: input fails {record['schema']} at {where}: "
                f"{first.message}"
            )

    # The checks below read fields the record schema may already have
    # rejected, so each reads only values of the type that schema requires.
    ids = [record["id"] for record in records if isinstance(record.get("id"), str)]
    problems.extend(
        f"duplicate id {i!r}" for i in sorted({i for i in ids if ids.count(i) > 1})
    )
    if len(paths) < _MIN_VECTOR_FLOOR:
        problems.append(
            f"expected at least {_MIN_VECTOR_FLOOR} vectors, found {len(paths)}"
        )

    errors = [r["error"] for r in records if isinstance(r.get("error"), dict)]
    structure_reasons = record_schema["$defs"]["error"]["oneOf"][1]["properties"][
        "reason"
    ]["enum"]
    rejected = {e["reason"] for e in errors if isinstance(e.get("reason"), str)}
    problems.extend(
        f"no rejection vector for reason {reason!r}"
        for reason in structure_reasons
        if reason not in rejected
    )

    for subject in _SUBJECT_SCHEMAS:
        mine = [r for r in records if r.get("schema") == subject]
        if not any(r.get("expect") == "accept" for r in mine):
            problems.append(f"no accepting vector for schema {subject!r}")
        if not any(
            isinstance(r.get("error"), dict) and r["error"].get("by") == "schema"
            for r in mine
        ):
            problems.append(f"no schema rejection vector for schema {subject!r}")
    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s) in {PACKAGE_DIR}", file=sys.stderr)
        return 1
    count = sum(1 for p in VECTORS_DIR.glob("*.json") if p != SCHEMA_PATH)
    print(f"{count} step and description vectors valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
