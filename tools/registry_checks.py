"""Checks the registry validators share: strict JSON loading, schema
conformance, mirror byte-identity, reading a file at the latest release tag,
and reporting.

`validate_limit_registry.py`, `validate_unit_registry.py` and
`validate_warning_reasons.py` import this module, and `validate_registry.py`
takes its release-tag helpers from it. A PEP 723 script's own directory is on
`sys.path`, so `uv run tools/<validator>.py` finds it, and
`tools/tests/conftest.py` puts `tools/` on the path for the tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from collections.abc import Hashable, Iterable
from pathlib import Path
from typing import Any

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent


class RegistryFileError(Exception):
    """A file a validator reads is missing or is not strict JSON. The message
    names the file."""


def relative(path: Path) -> str:
    """`path` relative to the repo root when it lies inside it."""
    return (
        str(path.relative_to(REPO_ROOT))
        if path.is_relative_to(REPO_ROOT)
        else str(path)
    )


def duplicates[T: Hashable](values: Iterable[T]) -> list[T]:
    """Every value that appears more than once in `values`, sorted."""
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    repeated = duplicates(key for key, _ in pairs)
    if repeated:
        raise ValueError(f"duplicate keys {repeated}")
    return dict(pairs)


def parse_json(text: str) -> Any:
    """Parse `text`, refusing an object that repeats a key. Raises `ValueError`."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def load_json(path: Path) -> Any:
    """Read and parse `path` once, refusing an object that repeats a key.

    Raises `RegistryFileError` naming the file when it is missing, is not
    JSON, or repeats a key.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RegistryFileError(f"{relative(path)} does not exist") from exc
    try:
        return parse_json(text)
    except ValueError as exc:
        raise RegistryFileError(f"{relative(path)} is not valid JSON: {exc}") from exc


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


def check_mirror(canonical: Path, mirror: Path) -> list[str]:
    """The package mirror must be byte-identical to the canonical file."""
    for path in (canonical, mirror):
        if not path.is_file():
            return [f"{relative(path)} does not exist"]
    if mirror.read_bytes() != canonical.read_bytes():
        return [f"{relative(mirror)} is not byte-identical to {relative(canonical)}"]
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


def json_at_tag(tag: str, relative_path: str) -> Any | None:
    """Return the parsed file at `relative_path` as it existed at `tag`, or
    None when the file is absent at the tag or unreadable."""
    result = subprocess.run(
        ["git", "show", f"{tag}:{relative_path}"],
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


def report(problems: list[str], *, ok: str, notices: Iterable[str] = ()) -> int:
    """Print each notice as a NOTE line and each problem as a FAIL line, or
    `ok` when there are none, and return the exit status."""
    for notice in notices:
        print(f"NOTE: {notice}")
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"OK: {ok}")
    return 0
