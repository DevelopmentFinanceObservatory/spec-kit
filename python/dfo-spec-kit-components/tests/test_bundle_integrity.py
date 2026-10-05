"""Structural conformance for the packaged catalogue bundle
(`src/dfo_spec_kit_components/catalogue/`).

Checks what every artifact owes regardless of role: every JSON file
parses, every schema file (`*.schema.json` and the intent grammar) is a
well-formed JSON Schema draft 2020-12 document, the manifest's digest map
names exactly the bundle's JSON artifacts, and every digest is
byte-correct. Grammar and envelope conformance live in
`test_intent_grammar.py` and `test_envelope_fixtures.py`.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json

import pytest
from jsonschema import Draft202012Validator

from dfo_spec_kit_components import bundle_dir

_BUNDLE = bundle_dir()

_SCHEMA_SUFFIXES = (".schema.json",)


def _all_json_files() -> list[importlib.resources.abc.Traversable]:
    root = importlib.resources.files("dfo_spec_kit_components") / "catalogue"
    found: list[importlib.resources.abc.Traversable] = []

    def _walk(directory: importlib.resources.abc.Traversable) -> None:
        for entry in directory.iterdir():
            if entry.is_dir():
                _walk(entry)
            elif entry.name.endswith(".json"):
                found.append(entry)

    _walk(root)
    return sorted(found, key=str)


_JSON_FILES = _all_json_files()


def test_bundle_is_non_empty() -> None:
    assert _JSON_FILES, "no JSON artifacts found under the packaged catalogue bundle"


@pytest.mark.parametrize(
    "entry",
    _JSON_FILES,
    ids=[str(e).rsplit("catalogue", maxsplit=1)[-1] for e in _JSON_FILES],
)
def test_every_json_artifact_parses(entry: importlib.resources.abc.Traversable) -> None:
    with entry.open("rb") as f:
        json.load(f)


def _is_schema_file(name: str) -> bool:
    return name.endswith(_SCHEMA_SUFFIXES) or name == "intent.v0.json"


_SCHEMA_FILES = [entry for entry in _JSON_FILES if _is_schema_file(entry.name)]


def test_schema_files_were_discovered() -> None:
    """A guard against a refactor that silently stops finding schema files.
    The bundle ships eight schema files (envelope, six component schemas,
    the intent grammar); the `>= 8` threshold leaves room to add more
    without touching this test."""
    assert len(_SCHEMA_FILES) >= 8


@pytest.mark.parametrize("entry", _SCHEMA_FILES, ids=[e.name for e in _SCHEMA_FILES])
def test_schema_files_conform_to_the_2020_12_metaschema(
    entry: importlib.resources.abc.Traversable,
) -> None:
    with entry.open("rb") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)


def test_manifest_covers_exactly_the_bundles_json_artifacts() -> None:
    manifest = json.loads((_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    manifest_paths = set(manifest["artifacts"])

    on_disk_paths = {
        p.relative_to(_BUNDLE).as_posix()
        for p in _BUNDLE.rglob("*.json")
        if p.name != "manifest.json"
    }

    missing_digests = on_disk_paths - manifest_paths
    stale_entries = manifest_paths - on_disk_paths
    assert not missing_digests, (
        f"artifacts with no manifest digest: {sorted(missing_digests)}"
    )
    assert not stale_entries, (
        f"manifest digests with no matching artifact: {sorted(stale_entries)}"
    )


def test_every_manifest_digest_verifies() -> None:
    manifest = json.loads((_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    mismatches = []
    for relative_path, expected in manifest["artifacts"].items():
        actual = f"sha256:{hashlib.sha256((_BUNDLE / relative_path).read_bytes()).hexdigest()}"
        if actual != expected:
            mismatches.append((relative_path, expected, actual))
    assert not mismatches, f"digest mismatches: {mismatches}"
