# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate registries/codes.json against schemas/registry/code-registry.schema.json.

Run with: uv run tools/validate_registry.py

Beyond schema conformance (which covers field shapes, the lower-snake-case
code pattern, and the closed endpoint/door/channel vocabulary), this checks
what a JSON Schema cannot express on its own: code uniqueness, sort order,
classification/channel consistency, byte-identity between the canonical
registry and its packaged mirror (`python/spec-kit/src/dfo_spec_kit/registry/
codes.json`), and — once a release tag exists — immutability of every
previously published entry, other than the narrow, named deletion exception
in `DELETION_ALLOWED` and a published entry's `mappings`, which may grow (see
check_mapping_growth).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry" / "code-registry.schema.json"
REGISTRY_PATH = REPO_ROOT / "registries" / "codes.json"
REGISTRY_RELATIVE_PATH = "registries/codes.json"
MIRROR_PATH = REPO_ROOT / "python/spec-kit/src/dfo_spec_kit/registry/codes.json"
MIRROR_RELATIVE_PATH = "python/spec-kit/src/dfo_spec_kit/registry/codes.json"

REFUSAL_CHANNELS = {
    "problem_details",
    "no_data_reason",
    "unsupported_code",
    "flagged_match_gate",
    # A returned Spec's observations.omitted.reason, the same non-warning
    # reason-string modelling no_data_reason gives a whole response's status.
    # Also a WARNING_CHANNELS member: observations_preview_too_large,
    # observations_windowed_scope_unsupported and
    # observations_converted_scope_unsupported are warning-classified codes
    # that also ride observations.omitted.reason (see WARNING_CHANNELS below),
    # so this channel legally carries either classification depending on the
    # entry.
    "observations_omission_reason",
    # The refusal envelope of an MCP tool result, which has no HTTP status.
    "mcp_refusal",
}
WARNING_CHANNELS = {
    "diagnostics_warning",
    "done_event_warning",
    "manifest_warning",
    "skills_warning",
    # See the matching entry (and comment) in REFUSAL_CHANNELS above:
    # observations_preview_too_large, observations_windowed_scope_unsupported
    # and observations_converted_scope_unsupported are warning-classified
    # codes that also ride observations.omitted.reason, so their mappings need
    # this channel legal under classification == "warning" too.
    "observations_omission_reason",
}

# A published entry is otherwise immutable: a code that stops being emitted
# moves to lifecycle: retired, which keeps the spelling reserved (see
# check_immutability and schemas/registry/code-registry.schema.json). This is
# the one narrow, reviewed exception to that rule: a code named here may be
# deleted outright rather than retired, because its producing engine was
# removed before any consumer ever received an occurrence, so no spelling
# actually needs reserving. Adding a code here is a deliberate, reviewed
# change.
DELETION_ALLOWED = {
    "purpose_broadened_to_set",
    "purpose_breadth_measured",
}

# check_mapping_growth's own "doors only ever grow" rule is the door-level
# analog of the immutability check above, with the same shape of exception:
# a door the schema's own enum no longer defines (schema_legal_doors) was
# retired from the schema entirely (schemas/registry/code-registry.schema.json
# no longer lists it as a legal `doors` item on any endpoint), so a published
# mapping dropping it is the schema's own retirement propagating through, not
# a live client losing access to a code it could see before.
# schema_legal_doors derives its set structurally from the schema file
# itself, unlike DELETION_ALLOWED's curated set, because retiring a door is a
# schema change, reviewed as one when the schema is edited (see
# schemas/registry/README.md).


class ValidationError(Exception):
    """Raised to collect a validation error for reporting; not a control-flow signal."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ValidationError(f"{path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc


def check_schema(registry: Any, schema: Any) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(registry), key=lambda e: list(e.absolute_path)
    )
    return [
        f"schema: {'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]


def _mapping_key(mapping: dict[str, Any]) -> tuple[Any, Any]:
    """A mapping's identity within its entry: its endpoint and channel.

    An entry's `mappings` are one per `(endpoint, channel)` pair; a code that
    surfaces on the same endpoint/channel pair through several doors names
    every door in that one mapping's `doors` list rather than repeating the
    pair. `check_immutability` matches mappings across tags by this same key,
    and relies on each side carrying no duplicate of it.
    """
    return (mapping.get("endpoint"), mapping.get("channel"))


def check_entries(entries: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    codes = [e.get("code", "") for e in entries]

    seen: set[str] = set()
    duplicates = {c for c in codes if c in seen or seen.add(c)}
    if duplicates:
        problems.append(f"duplicate code(s): {', '.join(sorted(duplicates))}")

    if codes != sorted(codes):
        for i, (actual, expected) in enumerate(zip(codes, sorted(codes), strict=True)):
            if actual != expected:
                problems.append(
                    f"entries are not sorted by code: position {i} has {actual!r}, "
                    f"expected {expected!r}"
                )
                break

    for entry in entries:
        code = entry.get("code", "<missing>")
        classification = entry.get("classification")
        mappings = entry.get("mappings", [])

        seen_keys: set[tuple[Any, Any]] = set()
        for mapping in mappings:
            key = _mapping_key(mapping)
            if key in seen_keys:
                problems.append(
                    f"{code}: mapping onto {key[0]!r}/{key[1]!r} repeated; a "
                    "code with several doors on one endpoint/channel pair "
                    "names them in one mapping's doors list"
                )
            seen_keys.add(key)

            channel = mapping.get("channel")
            if classification == "refusal" and channel not in REFUSAL_CHANNELS:
                problems.append(
                    f"{code}: refusal-classified entry uses warning channel {channel!r}"
                )
            if classification == "warning" and channel not in WARNING_CHANNELS:
                problems.append(
                    f"{code}: warning-classified entry uses refusal channel {channel!r}"
                )

    return problems


def check_mirror() -> list[str]:
    """`registries/codes.json` is canonical; the package mirror at
    `MIRROR_RELATIVE_PATH` must stay byte-for-byte identical (uv_build cannot
    include files from outside the package directory, so the package carries
    its own copy rather than a path reference)."""
    try:
        canonical_bytes = REGISTRY_PATH.read_bytes()
    except FileNotFoundError:
        return [f"{REGISTRY_RELATIVE_PATH} does not exist"]

    try:
        mirror_bytes = MIRROR_PATH.read_bytes()
    except FileNotFoundError:
        return [
            f"{MIRROR_RELATIVE_PATH} does not exist; copy {REGISTRY_RELATIVE_PATH} to "
            f"{MIRROR_RELATIVE_PATH} to resync"
        ]

    if canonical_bytes != mirror_bytes:
        return [
            f"{MIRROR_RELATIVE_PATH} has drifted from the canonical "
            f"{REGISTRY_RELATIVE_PATH}; copy {REGISTRY_RELATIVE_PATH} over "
            f"{MIRROR_RELATIVE_PATH} to resync (e.g. "
            f"`cp {REGISTRY_RELATIVE_PATH} {MIRROR_RELATIVE_PATH}`)"
        ]
    return []


def latest_release_tag() -> str | None:
    """Return the most recent tag reachable from HEAD, or None if there isn't one."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    tag = result.stdout.strip()
    return tag or None


def registry_at_tag(tag: str) -> Any | None:
    """Return the parsed registry as it existed at `tag`, or None if unreadable."""
    result = subprocess.run(
        ["git", "show", f"{tag}:{REGISTRY_RELATIVE_PATH}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _doors_set(mapping: dict[str, Any]) -> frozenset[str] | None:
    """A mapping's `doors` as a set, or None when `doors` is omitted.

    The schema documents an omitted `doors` as "applies regardless of door":
    the universal set, not the empty one.
    """
    doors = mapping.get("doors")
    return frozenset(doors) if doors is not None else None


def _doors_grew_or_held(
    old_doors: frozenset[str] | None, new_doors: frozenset[str] | None
) -> bool:
    """True when `new_doors` covers every door `old_doors` covered.

    None (unrestricted) is a superset of any concrete door list, so a mapping
    may widen from a specific list to unrestricted, or gain doors within a
    list, but may not narrow from unrestricted to a specific list or drop a
    door from its list.
    """
    if old_doors is None:
        return new_doors is None
    if new_doors is None:
        return True
    return old_doors <= new_doors


def _schema_door_enums(schema: Any) -> tuple[frozenset[str], frozenset[str]]:
    """The schema's two sites that enumerate legal `doors` values.

    `$defs.mapping.properties.doors.items.enum` is the general vocabulary
    every mapping's `doors` are drawn from. The `allOf` clause conditioned on
    `endpoint == "search.resolve"` narrows that vocabulary further to
    `search.resolve`'s own subset (excluding `options`, legal only for
    `search.options`'s own const-doors clause). Returns (general, resolve).
    """
    mapping_schema = schema["$defs"]["mapping"]
    general = frozenset(mapping_schema["properties"]["doors"]["items"]["enum"])
    resolve_enum: frozenset[str] | None = None
    for clause in mapping_schema.get("allOf", []):
        endpoint_const = (
            clause.get("if", {}).get("properties", {}).get("endpoint", {}).get("const")
        )
        if endpoint_const == "search.resolve":
            resolve_enum = frozenset(
                clause["then"]["properties"]["doors"]["items"]["enum"]
            )
            break
    if resolve_enum is None:
        raise ValidationError(
            "schema: could not find the search.resolve doors override in "
            "$defs.mapping.allOf"
        )
    return general, resolve_enum


def schema_legal_doors(schema: Any) -> frozenset[str]:
    """The full set of `doors` values the schema currently recognises as legal.

    Retiring a door is a schema change, reviewed as one: dropping it from the
    door enum entirely (no longer a legal `doors` item on any endpoint) is
    how a door retires (see schemas/registry/README.md). `_schema_door_enums`
    reads the schema's two enum sites; the `search.resolve`-specific site is
    always a subset of the general one, since it only ever narrows the
    general vocabulary further for that one endpoint. If it names a door the
    general site does not, the schema disagrees with itself, and this raises
    loudly rather than silently trusting one site over the other.

    check_mapping_growth uses this set to tell a schema-retired door
    (legitimately absent from a tagged mapping's carried-forward `doors`)
    apart from a live client losing access to a door it could see before —
    it releases every mapping that named only that door.
    """
    general, resolve_enum = _schema_door_enums(schema)
    unexpected = resolve_enum - general
    if unexpected:
        raise ValidationError(
            "schema: search.resolve's doors override names "
            f"{sorted(unexpected)}, absent from the general doors enum at "
            "$defs.mapping.properties.doors.items.enum; the schema's two "
            "door-enum sites disagree"
        )
    return general


def check_mapping_growth(
    code: str,
    old_mappings: list[dict[str, Any]],
    new_mappings: list[dict[str, Any]],
    classification: str | None,
    tag: str,
    legal_doors: frozenset[str],
) -> list[str]:
    """Check one entry's `mappings` growth against its tagged counterpart.

    Mappings are matched by `(endpoint, channel)`. A matched pair keeps the
    same `http_status` and its `doors` only ever grow. A key present only at
    the tag is a removal. A key present only in the working copy is new
    growth, permitted exactly when its channel is legal for `classification`
    under the REFUSAL_CHANNELS/WARNING_CHANNELS coupling `check_entries`
    already enforces on the full registry.

    `legal_doors` (see schema_legal_doors) is the schema's current door
    vocabulary. A door the tagged mapping published that `legal_doors` no
    longer recognises has been retired from the schema itself, and is
    discounted from the tagged side of both the growth and mapping-removed
    comparisons below — the working copy's own doors are never discounted,
    since they were already validated against the current schema.
    """
    problems: list[str] = []
    old_by_key = {_mapping_key(m): m for m in old_mappings}
    new_by_key = {_mapping_key(m): m for m in new_mappings}
    allowed_channels = (
        REFUSAL_CHANNELS
        if classification == "refusal"
        else WARNING_CHANNELS
        if classification == "warning"
        else set()
    )

    for key, old_mapping in old_by_key.items():
        endpoint, channel = key
        new_mapping = new_by_key.get(key)
        old_doors = _doors_set(old_mapping)
        old_doors_live = None if old_doors is None else old_doors & legal_doors
        if new_mapping is None:
            if old_doors is not None and not old_doors_live:
                # Every door this mapping published has since been retired
                # from the schema's own enum (schema_legal_doors) -- its
                # removal is that retirement propagating through, not a
                # live client losing a code it could see before.
                continue
            problems.append(
                f"{code}: mapping onto {endpoint!r}/{channel!r} removed "
                f"(present at tag {tag!r})"
            )
            continue

        old_status = old_mapping.get("http_status")
        new_status = new_mapping.get("http_status")
        if old_status != new_status:
            problems.append(
                f"{code}: mapping onto {endpoint!r}/{channel!r} http_status "
                f"changed from {old_status!r} to {new_status!r}"
            )

        new_doors = _doors_set(new_mapping)
        if not _doors_grew_or_held(old_doors_live, new_doors):
            if old_doors_live is None:
                problems.append(
                    f"{code}: mapping onto {endpoint!r}/{channel!r} doors "
                    f"restricted to {sorted(new_doors)}; unrestricted at tag {tag!r}"
                )
            else:
                dropped = sorted(old_doors_live - (new_doors or frozenset()))
                problems.append(
                    f"{code}: mapping onto {endpoint!r}/{channel!r} dropped "
                    f"door(s) {dropped} present at tag {tag!r}"
                )

    for key in new_by_key:
        if key in old_by_key:
            continue
        endpoint, channel = key
        if channel not in allowed_channels:
            problems.append(
                f"{code}: new mapping onto {endpoint!r}/{channel!r} is not "
                f"permitted for a {classification!r}-classified entry"
            )

    return problems


def check_immutability(
    entries: list[dict[str, Any]], legal_doors: frozenset[str]
) -> tuple[str | None, list[str]]:
    """Compare the working registry against the latest release tag's published entries.

    Every published field is frozen once tagged, other than `lifecycle` and
    `mappings`. `mappings` is allowed to grow; check_mapping_growth defines
    what growth means and what still counts as a violation. `legal_doors`
    (see schema_legal_doors) is threaded through to check_mapping_growth so a
    schema-retired door doesn't read as a live client losing access.

    Returns (notice, problems). notice is an informational message printed on
    stdout (e.g. when there is no tag yet); it is never a failure. problems is
    the list of immutability violations, each of which fails the run.
    """
    tag = latest_release_tag()
    if tag is None:
        return "no release tag yet; immutability check skipped", []

    old_registry = registry_at_tag(tag)
    if old_registry is None:
        return (
            f"could not read {REGISTRY_RELATIVE_PATH} at tag {tag!r}; immutability check skipped",
            [],
        )

    old_by_code = {e["code"]: e for e in old_registry.get("entries", []) if "code" in e}
    new_by_code = {e["code"]: e for e in entries if "code" in e}

    problems: list[str] = []
    for code, old_entry in old_by_code.items():
        new_entry = new_by_code.get(code)
        if new_entry is None:
            if code not in DELETION_ALLOWED:
                problems.append(
                    f"{code}: published entry was deleted (present at tag {tag!r})"
                )
            continue

        old_rest = {
            k: v for k, v in old_entry.items() if k not in ("lifecycle", "mappings")
        }
        new_rest = {
            k: v for k, v in new_entry.items() if k not in ("lifecycle", "mappings")
        }
        if old_rest != new_rest:
            problems.append(
                f"{code}: a published field other than lifecycle or mappings "
                f"changed since tag {tag!r}"
            )

        problems.extend(
            check_mapping_growth(
                code,
                old_entry.get("mappings", []),
                new_entry.get("mappings", []),
                new_entry.get("classification"),
                tag,
                legal_doors,
            )
        )

        old_lifecycle = old_entry.get("lifecycle")
        new_lifecycle = new_entry.get("lifecycle")
        if old_lifecycle == "retired" and new_lifecycle != "retired":
            problems.append(
                f"{code}: lifecycle moved from retired to {new_lifecycle!r}; retired is terminal"
            )

    return None, problems


def main() -> int:
    try:
        schema = load_json(SCHEMA_PATH)
        registry = load_json(REGISTRY_PATH)
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    problems = check_schema(registry, schema)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    problems = check_entries(registry.get("entries", []))
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    problems = check_mirror()
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    try:
        legal_doors = schema_legal_doors(schema)
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    notice, problems = check_immutability(registry.get("entries", []), legal_doors)
    if notice:
        print(f"NOTE: {notice}")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    print(f"OK: {len(registry['entries'])} registry entries validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
