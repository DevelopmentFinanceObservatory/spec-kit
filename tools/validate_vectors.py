# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
#     "rfc8785>=0.1.4",
# ]
# ///
"""Validate every file under vectors/identity/ against schemas/vectors/identity-vector.schema.json.

Run with: uv run tools/validate_vectors.py

Beyond schema conformance (which covers field shapes, the positive/negative
structural split, the input/input_bytes exclusivity, and the closed
failure_category vocabulary), this checks what a JSON Schema cannot express
on its own: filename-vs-name consistency, name uniqueness, and -- for every
positive vector -- that canonical_bytes is genuinely JCS(expected_projection)
and that spec_id is genuinely "spec_" plus the first 32 lowercase hex
characters of SHA-256(canonical_bytes). It does not implement the identity
algorithm's normalisation steps -- the stages that build expected_projection
from input/input_bytes -- so it does not check expected_projection against
input/input_bytes; that would mean re-implementing the reference identity
algorithm rather than validating the fixture format.

spec_id is deliberately NOT required to be unique across the suite: the
facets-explicit-empty-object-converges/facets-omitted-member-converges pair
shares one spec_id on purpose, proving two distinct spellings of "no facets
bound" converge on one id. Only `name` is required unique.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import jsonschema
import rfc8785

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "vectors" / "identity-vector.schema.json"
VECTORS_DIR = REPO_ROOT / "vectors" / "identity"


class ValidationError(Exception):
    """Raised to collect a validation error for reporting; not a control-flow signal."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen.add(key)
        result[key] = value
    return result


def _reject_non_finite_constant(constant: str) -> Any:
    # json.loads calls this for the bare NaN/Infinity/-Infinity tokens; a strict
    # fixture format must not silently accept what I-JSON forbids.
    raise ValueError(f"non-finite number literal {constant!r}")


def _check_finite(value: Any, where: str = "<root>") -> None:
    # A number literal like 1e400 parses without hitting parse_constant at all --
    # Python's float() silently overflows it to inf -- so finiteness needs a
    # second, structural pass after parsing, not just the parse_constant hook.
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float at {where}: {value}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_finite(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_finite(item, f"{where}[{index}]")


def load_json(path: Path) -> Any:
    try:
        text = path.read_text()
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
        _check_finite(data)
    except FileNotFoundError as exc:
        raise ValidationError(f"{path} does not exist") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc
    return data


def check_schema(record: Any, validator: jsonschema.Draft202012Validator) -> list[str]:
    errors = sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path))
    return [
        f"schema: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]


def check_filename(path: Path, record: dict[str, Any]) -> list[str]:
    problems = []
    name = record.get("name")
    if name is not None and name != path.stem:
        problems.append(f"filename {path.name!r} does not match record name {name!r}")
    return problems


def check_consistency(record: dict[str, Any]) -> list[str]:
    """Recompute canonical_bytes and spec_id from expected_projection and compare."""
    problems: list[str] = []
    if record.get("classification") != "positive":
        return problems
    projection = record.get("expected_projection")
    if projection is None:
        return problems  # already flagged by schema check

    try:
        recomputed_bytes = rfc8785.dumps(projection)
    except Exception as exc:  # noqa: BLE001 - report any JCS failure as a vector problem
        return [f"expected_projection does not JCS-serialise: {exc}"]

    recomputed_hex = recomputed_bytes.hex()
    committed_hex = record.get("canonical_bytes")
    if committed_hex != recomputed_hex:
        problems.append(
            f"canonical_bytes mismatch: committed {committed_hex!r} != "
            f"JCS(expected_projection) {recomputed_hex!r}"
        )

    recomputed_spec_id = "spec_" + hashlib.sha256(recomputed_bytes).hexdigest()[:32]
    committed_spec_id = record.get("spec_id")
    if committed_spec_id != recomputed_spec_id:
        problems.append(
            f"spec_id mismatch: committed {committed_spec_id!r} != "
            f"spec_ + sha256(canonical_bytes)[:32] {recomputed_spec_id!r}"
        )

    return problems


def check_input_bytes_hex(record: dict[str, Any]) -> list[str]:
    """Confirm input_bytes, when present, is well-formed hex (beyond the schema's regex, decode it)."""
    raw = record.get("input_bytes")
    if raw is None:
        return []
    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        return [f"input_bytes is not valid hex: {exc}"]
    return []


def main() -> int:
    try:
        schema = load_json(SCHEMA_PATH)
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    paths = sorted(VECTORS_DIR.glob("*.json"))
    if not paths:
        print(f"FAIL: no vector files found under {VECTORS_DIR}", file=sys.stderr)
        return 1

    all_problems: dict[str, list[str]] = {}
    names_seen: dict[str, Path] = {}
    classifications: list[str] = []

    for path in paths:
        problems: list[str] = []
        try:
            record = load_json(path)
        except ValidationError as exc:
            all_problems[path.name] = [str(exc)]
            continue

        name = record.get("name")
        if isinstance(name, str):
            if name in names_seen:
                problems.append(f"duplicate name {name!r} (also used by {names_seen[name].name})")
            else:
                names_seen[name] = path

        problems += check_schema(record, validator)
        if problems:
            # Structural failures make every further check unreliable; report and move on.
            all_problems[path.name] = problems
            continue

        problems += check_filename(path, record)
        problems += check_input_bytes_hex(record)
        problems += check_consistency(record)

        if problems:
            all_problems[path.name] = problems
        else:
            classifications.append(record.get("classification"))

    if all_problems:
        for filename, problems in sorted(all_problems.items()):
            for problem in problems:
                print(f"FAIL: {filename}: {problem}", file=sys.stderr)
        return 1

    positive_count = sum(1 for c in classifications if c == "positive")
    negative_count = sum(1 for c in classifications if c != "positive")

    print(
        f"OK: {len(paths)} vectors validated "
        f"({positive_count} positive, {negative_count} negative)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
