# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate every vector under
`python/provenance-docs/src/dfo_provenance_docs/vectors/` against
`provenance-docs-vector.schema.json`, beside it.

Usage:
    uv run tools/validate_provenance_docs_vectors.py

Beyond schema conformance, this checks what the schema cannot express: each
file name matches its record's `id`, ids are unique, the suite meets its
floor, every reason in the schema's vocabulary has at least one rejection
vector, and every function has at least one accepting vector. It does not
import `dfo_provenance_docs`. `python/provenance-docs/tests/test_vectors.py`
runs the implementation against the same files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
VECTORS_DIR = (
    REPO_ROOT / "python" / "provenance-docs" / "src" / "dfo_provenance_docs" / "vectors"
)
SCHEMA_PATH = VECTORS_DIR / "provenance-docs-vector.schema.json"

_MIN_VECTOR_FLOOR = 60


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


def validate() -> list[str]:
    schema = _load(SCHEMA_PATH)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    paths = sorted(p for p in VECTORS_DIR.glob("*.json") if p != SCHEMA_PATH)
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
            if record.get("id") != path.stem:
                problems.append(
                    f"{path.name}: id {record.get('id')!r} does not match the file name"
                )

    # The checks below read fields the schema may already have rejected, so
    # each reads only values of the type the schema requires and leaves the
    # rest to the schema diagnostics above.
    ids = [record["id"] for record in records if isinstance(record.get("id"), str)]
    problems.extend(
        f"duplicate id {i!r}" for i in sorted({i for i in ids if ids.count(i) > 1})
    )
    if len(paths) < _MIN_VECTOR_FLOOR:
        problems.append(
            f"expected at least {_MIN_VECTOR_FLOOR} vectors, found {len(paths)}"
        )

    vocabulary = schema["$defs"]["error"]["properties"]["reason"]["enum"]
    rejected = {
        r["error"]["reason"]
        for r in records
        if isinstance(r.get("error"), dict)
        and isinstance(r["error"].get("reason"), str)
    }
    problems.extend(
        f"no rejection vector for reason {reason!r}"
        for reason in vocabulary
        if reason not in rejected
    )

    functions = schema["properties"]["function"]["enum"]
    accepted = {
        r["function"]
        for r in records
        if "expect" in r and isinstance(r.get("function"), str)
    }
    problems.extend(
        f"no accepting vector for function {name!r}"
        for name in functions
        if name not in accepted
    )
    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s) in {VECTORS_DIR}", file=sys.stderr)
        return 1
    count = sum(1 for p in VECTORS_DIR.glob("*.json") if p != SCHEMA_PATH)
    print(f"{count} provenance docs vectors valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
