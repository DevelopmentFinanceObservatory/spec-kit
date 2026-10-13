"""Structural conformance for the packaged catalogue bundle
(`src/dfo_spec_kit_components/catalogue/`).

Checks what every artifact owes regardless of role: every JSON file
parses, every schema file (`*.schema.json` and the intent grammar) is a
well-formed JSON Schema draft 2020-12 document, the manifest's digest map
names exactly the bundle's JSON artifacts, and every digest is
byte-correct. The manifest also digests, by `$id`, every schema outside the
bundle that a bundle schema references, directly or through another such
schema, so a change to one changes the manifest. Grammar and envelope conformance live in
`test_intent_grammar.py` and `test_envelope_fixtures.py`.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
from collections.abc import Iterator
from urllib.parse import urldefrag, urljoin

import pytest
from dfo_descriptions import schema_dir
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


def _referenced_documents(node: object, base: str) -> Iterator[str]:
    """The `$id` of every document a `$ref` under `node` points into,
    resolved against `base`, the `$id` of the schema holding it."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield urldefrag(urljoin(base, ref)).url
        for value in node.values():
            yield from _referenced_documents(value, base)
    elif isinstance(node, list):
        for value in node:
            yield from _referenced_documents(value, base)


def _description_schema_bytes() -> dict[str, bytes]:
    """The installed descriptions package's schemas, by `$id`."""
    found = {}
    for entry in schema_dir().glob("*.schema.json"):
        raw = entry.read_bytes()
        found[json.loads(raw)["$id"]] = raw
    return found


def test_manifest_digests_every_schema_outside_the_bundle_it_references() -> None:
    """`referenced_schemas` names the closure of out-of-bundle schemas the
    bundle's schemas reach by `$ref`, and each digest matches the installed
    bytes, so a change that decides which payloads validate changes the
    manifest."""
    manifest = json.loads((_BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    bundle_schemas = {}
    for entry in _SCHEMA_FILES:
        with entry.open("rb") as f:
            schema = json.load(f)
        bundle_schemas[schema["$id"]] = schema
    external = _description_schema_bytes()
    reached: set[str] = set()
    pending = [
        target
        for schema_id, schema in bundle_schemas.items()
        for target in _referenced_documents(schema, schema_id)
        if target not in bundle_schemas
    ]
    while pending:
        target = pending.pop()
        if target in reached:
            continue
        reached.add(target)
        assert target in external, f"no installed schema has $id {target!r}"
        pending.extend(_referenced_documents(json.loads(external[target]), target))
    reached -= set(bundle_schemas)
    assert reached, "expected the envelope to reference the description schema"
    digests = {
        schema_id: f"sha256:{hashlib.sha256(external[schema_id]).hexdigest()}"
        for schema_id in reached
    }
    assert manifest["referenced_schemas"] == digests
