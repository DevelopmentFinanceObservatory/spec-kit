# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
#     "rfc8785>=0.1.4",
# ]
# ///
"""Validate every file under
`python/frame-grammar/src/dfo_frame_grammar/vectors/frame/` against
`frame-vector.schema.json`, beside it.

Usage:
    uv run tools/validate_frame_vectors.py

Beyond schema conformance (field shapes, the "frame"/"content_version"/
"collection_content_version" kind split, the positive/negative structural
split, and the closed failure_category vocabulary), this checks what a JSON
Schema cannot express on its own: filename-vs-name consistency, name
uniqueness, a minimum vector-count floor, that both classifications are
non-empty, and -- for every positive vector -- that its stored bytes and
tokens are genuinely self-consistent:

- kind "frame": canonical_bytes is genuinely JCS(expected_document), the
  expected_document's own columns match expected_columns, expected_frame_sha256
  is genuinely SHA-256(canonical_bytes), expected_preimage's own frame_sha256
  matches expected_frame_sha256, and expected_content_version is genuinely
  "cv_" plus the first 32 lowercase hex characters of
  SHA-256(JCS(expected_preimage)).
- kind "content_version": expected_preimage's own frame_sha256 matches the
  vector's frame_sha256, and expected_content_version is genuinely derived
  from expected_preimage the same way.
- kind "collection_content_version": expected_preimage's members are sorted
  by spec_id (a structural check on the vector's own stated data, not a claim
  about how any implementation built it), and expected_content_version is
  genuinely derived from expected_preimage the same way.

This script deliberately does NOT import dfo_frame_grammar, directly or
transitively -- see _assert_never_imported_dfo_frame_grammar below. It does not
re-implement Frame.build's row construction or content_version's preimage
assembly, so it never checks expected_document/expected_preimage against
role_keys/rows/content_version_input through those rules. That is
python/frame-grammar/tests/test_vectors.py's job, run against the real
implementation; this script only checks that a vector's own committed
fields hang together internally, independent of any implementation.
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
VECTORS_DIR = (
    REPO_ROOT / "python" / "frame-grammar" / "src" / "dfo_frame_grammar" / "vectors" / "frame"
)
SCHEMA_PATH = VECTORS_DIR / "frame-vector.schema.json"

_MIN_VECTOR_FLOOR = 20


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
    raise ValueError(f"non-finite number literal {constant!r}")


def _check_finite(value: Any, where: str = "<root>") -> None:
    # A number literal like 1e400 parses without ever hitting parse_constant --
    # Python's float() silently overflows it to inf -- so finiteness needs a
    # second, structural pass after parsing.
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


def check_negative_shape(record: dict[str, Any]) -> list[str]:
    """Redundant with the schema's own if/then, kept explicit here so a
    negative vector's shape violation is reported by name rather than only
    by a generic schema error: a negative vector carries `failure_category`
    and nothing a positive vector would carry."""
    if record.get("classification") != "negative":
        return []
    problems: list[str] = []
    if not record.get("failure_category"):
        problems.append("negative vector has no failure_category")
    for field in (
        "expected_columns",
        "canonical_bytes",
        "expected_document",
        "expected_frame_sha256",
        "expected_preimage",
        "expected_content_version",
    ):
        if field in record:
            problems.append(f"negative vector carries positive-only field {field!r}")
    return problems


def _preimage_content_version(preimage: dict[str, Any]) -> str:
    canonical = rfc8785.dumps(preimage)
    digest = hashlib.sha256(canonical).hexdigest()
    return f"cv_{digest[:32]}"


def check_consistency(record: dict[str, Any]) -> list[str]:
    """Recompute every byte/token relationship a positive vector claims,
    straight from that vector's own committed fields -- never from role_keys/
    rows/content_version_input, which would mean re-implementing Frame.build
    and the preimage builder rather than validating the fixture format."""
    if record.get("classification") != "positive":
        return []

    kind = record.get("kind")
    problems: list[str] = []

    if kind == "frame":
        expected_document = record.get("expected_document")
        expected_columns = record.get("expected_columns")
        canonical_bytes = record.get("canonical_bytes")
        expected_frame_sha256 = record.get("expected_frame_sha256")
        if expected_document is None or canonical_bytes is None:
            return problems  # already flagged by schema check

        if expected_columns is not None and expected_document.get("columns") != expected_columns:
            problems.append(
                "expected_document.columns does not match expected_columns: "
                f"{expected_document.get('columns')!r} != {expected_columns!r}"
            )

        try:
            recomputed_bytes = rfc8785.dumps(expected_document)
        except Exception as exc:  # noqa: BLE001 - report any JCS failure as a vector problem
            return [f"expected_document does not JCS-serialise: {exc}"]

        recomputed_hex = recomputed_bytes.hex()
        if canonical_bytes != recomputed_hex:
            problems.append(
                f"canonical_bytes mismatch: committed {canonical_bytes!r} != "
                f"JCS(expected_document) {recomputed_hex!r}"
            )

        recomputed_frame_sha256 = hashlib.sha256(recomputed_bytes).hexdigest()
        if expected_frame_sha256 != recomputed_frame_sha256:
            problems.append(
                f"expected_frame_sha256 mismatch: committed {expected_frame_sha256!r} != "
                f"SHA-256(canonical_bytes) {recomputed_frame_sha256!r}"
            )

        problems += _check_preimage_and_token(record, expected_digest=expected_frame_sha256)

    elif kind == "content_version":
        problems += _check_preimage_and_token(record, expected_digest=record.get("frame_sha256"))

    elif kind == "collection_content_version":
        preimage = record.get("expected_preimage")
        if preimage is None:
            return problems
        members = preimage.get("members")
        if isinstance(members, list):
            spec_ids = [member[0] for member in members if isinstance(member, list) and member]
            if spec_ids != sorted(spec_ids):
                problems.append(f"expected_preimage.members is not sorted by spec_id: {spec_ids!r}")
        problems += _check_token(record, preimage)

    return problems


def _check_preimage_and_token(record: dict[str, Any], *, expected_digest: str | None) -> list[str]:
    preimage = record.get("expected_preimage")
    if preimage is None:
        return []
    problems: list[str] = []
    if expected_digest is not None and preimage.get("frame_sha256") != expected_digest:
        problems.append(
            f"expected_preimage.frame_sha256 mismatch: {preimage.get('frame_sha256')!r} != "
            f"{expected_digest!r}"
        )
    problems += _check_token(record, preimage)
    return problems


def _check_token(record: dict[str, Any], preimage: dict[str, Any]) -> list[str]:
    committed_token = record.get("expected_content_version")
    if committed_token is None:
        return []
    try:
        recomputed_token = _preimage_content_version(preimage)
    except Exception as exc:  # noqa: BLE001 - report any JCS failure as a vector problem
        return [f"expected_preimage does not JCS-serialise: {exc}"]
    if committed_token != recomputed_token:
        return [
            f"expected_content_version mismatch: committed {committed_token!r} != "
            f"'cv_' + sha256(JCS(expected_preimage))[:32] {recomputed_token!r}"
        ]
    return []


def _assert_never_imported_dfo_frame_grammar() -> None:
    """This validator's entire point is independence from the implementation
    it validates fixtures for -- see this module's own docstring. A future
    edit that adds `import dfo_frame_grammar` (directly, or transitively through
    some new dependency) would silently turn this script into a circular
    check indistinguishable from the pytest harness it is supposed to be
    independent of. Asserted at both ends of main() so it catches an import
    added anywhere in this run, not just at module load time."""
    assert "dfo_frame_grammar" not in sys.modules, (
        "tools/validate_frame_vectors.py must never import dfo_frame_grammar, "
        "directly or transitively -- found it in sys.modules"
    )


def main() -> int:
    _assert_never_imported_dfo_frame_grammar()

    try:
        schema = load_json(SCHEMA_PATH)
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    paths = sorted(p for p in VECTORS_DIR.glob("*.json") if p != SCHEMA_PATH)
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

        name = record.get("name") if isinstance(record, dict) else None
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
        problems += check_negative_shape(record)
        problems += check_consistency(record)

        if problems:
            all_problems[path.name] = problems
        else:
            classifications.append(record["classification"])

    if len(paths) < _MIN_VECTOR_FLOOR:
        all_problems.setdefault("<suite>", []).append(
            f"only {len(paths)} vectors found, below the floor of {_MIN_VECTOR_FLOOR}"
        )

    positive_count = sum(1 for c in classifications if c == "positive")
    negative_count = sum(1 for c in classifications if c != "positive")
    if positive_count == 0:
        all_problems.setdefault("<suite>", []).append("no positive vectors found")
    if negative_count == 0:
        all_problems.setdefault("<suite>", []).append("no negative vectors found")

    if all_problems:
        for filename, problems in sorted(all_problems.items()):
            for problem in problems:
                print(f"FAIL: {filename}: {problem}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(paths)} vectors validated ({positive_count} positive, {negative_count} negative)"
    )
    _assert_never_imported_dfo_frame_grammar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
