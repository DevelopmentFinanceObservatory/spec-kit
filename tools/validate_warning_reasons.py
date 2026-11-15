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
its `code`, every `code` is a warning in registries/codes.json (an active
code for an active reason), and the packaged mirror
(`python/spec-kit/src/dfo_spec_kit/registry/warning-reasons.json`) is
byte-identical to the canonical file. Once a release tag is reachable from
HEAD, a reason published at that tag must still be present with the same
definition and params; it may move to `lifecycle: retired`, and retired is
terminal. It does not import `dfo_spec_kit`.
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
    json_at_tag,
    latest_release_tag,
    load_json,
    relative,
    report,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "registry" / "warning-reason-registry.schema.json"
REGISTRY_PATH = REPO_ROOT / "registries" / "warning-reasons.json"
CODES_PATH = REPO_ROOT / "registries" / "codes.json"
MIRROR_PATH = (
    REPO_ROOT / "python/spec-kit/src/dfo_spec_kit/registry/warning-reasons.json"
)

# Each registered code mapped to its codes.json entry.
type Codes = dict[str, dict[str, Any]]


def read_codes(codes_registry: Any) -> tuple[Codes, list[str]]:
    """Each code in a parsed codes.json, and a problem naming the file for
    every entry that lacks the members this validator reads."""
    where = relative(CODES_PATH)
    entries = (
        codes_registry.get("entries") if isinstance(codes_registry, dict) else None
    )
    if not isinstance(entries, list):
        return {}, [f"{where}: has no entries list"]
    codes: Codes = {}
    problems: list[str] = []
    for index, entry in enumerate(entries):
        missing = [
            key
            for key in ("code", "classification", "lifecycle")
            if not isinstance(entry, dict) or key not in entry
        ]
        if missing:
            problems.append(f"{where}: entries/{index} lacks {missing}")
        else:
            codes[entry["code"]] = entry
    return codes, problems


def check_entries(registry: dict[str, Any], codes: Codes) -> list[str]:
    """The rules a schema-valid registry must also meet."""
    keys = [(entry["code"], entry["reason"]) for entry in registry["entries"]]
    problems: list[str] = []
    if keys != sorted(keys):
        problems.append("entries is not sorted by code and reason")
    problems.extend(
        f"reason declared twice for {code!r}: {reason!r}"
        for code, reason in duplicates(keys)
    )
    for code in sorted({code for code, _ in keys}):
        code_entry = codes.get(code)
        if code_entry is None:
            problems.append(f"code is not in registries/codes.json: {code!r}")
        elif code_entry["classification"] != "warning":
            problems.append(f"code is not a warning: {code!r}")
    problems.extend(
        f"active reason {entry['reason']!r} names retired code {entry['code']!r}"
        for entry in registry["entries"]
        if entry["lifecycle"] == "active"
        and codes.get(entry["code"], {}).get("lifecycle") == "retired"
    )
    return problems


def check_published(
    entries: list[dict[str, Any]], published: list[dict[str, Any]], tag: str
) -> list[str]:
    """Every reason in `published` (the entries at `tag`) is still present with
    the same definition and params, and a retired one stays retired."""
    current = {(entry["code"], entry["reason"]): entry for entry in entries}
    problems: list[str] = []
    for old in published:
        label = f"{old['code']}/{old['reason']}"
        new = current.get((old["code"], old["reason"]))
        if new is None:
            problems.append(
                f"{label}: published reason was removed (present at tag {tag!r})"
            )
            continue
        problems.extend(
            f"{label}: {member} changed since tag {tag!r}"
            for member in ("definition", "params")
            if old.get(member) != new.get(member)
        )
        if old.get("lifecycle") == "retired" and new["lifecycle"] != "retired":
            problems.append(f"{label}: lifecycle left retired, which is terminal")
    return problems


def check_immutability(
    entries: list[dict[str, Any]],
) -> tuple[str | None, list[str]]:
    """Compare `entries` with the registry at the latest release tag.

    Returns (notice, problems). The notice, printed on stdout, says why the
    comparison was skipped; it is never a failure.
    """
    tag = latest_release_tag()
    if tag is None:
        return "no release tag yet; immutability check skipped", []
    path = relative(REGISTRY_PATH)
    published = json_at_tag(tag, path)
    if published is None:
        return f"could not read {path} at tag {tag!r}; immutability check skipped", []
    return None, check_published(entries, published.get("entries", []), tag)


def check_mirror() -> list[str]:
    return registry_checks.check_mirror(REGISTRY_PATH, MIRROR_PATH)


def validate() -> tuple[list[str], list[str], Any]:
    """The problems found, the notices to print, and the parsed registry (None
    when unreadable)."""
    try:
        schema = load_json(SCHEMA_PATH)
        registry = load_json(REGISTRY_PATH)
        codes_registry = load_json(CODES_PATH)
    except RegistryFileError as exc:
        return [str(exc)], [], None
    codes, problems = read_codes(codes_registry)
    problems += check_schema(registry, schema)
    if problems:
        return problems, [], registry
    notice, published_problems = check_immutability(registry["entries"])
    problems = check_entries(registry, codes) + check_mirror() + published_problems
    return problems, [notice] if notice else [], registry


def main() -> int:
    problems, notices, registry = validate()
    count = 0 if problems else len(registry["entries"])
    return report(problems, ok=f"{count} warning reasons validated", notices=notices)


if __name__ == "__main__":
    sys.exit(main())
