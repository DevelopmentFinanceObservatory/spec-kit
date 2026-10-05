"""Drive the implementation against the packaged vector suite.

The suite ships as package data at `dfo_provenance_docs/vectors/`, so it is
present wherever the package is installed and a missing or emptied directory
is a hard failure. The record format is defined in the README beside the
vectors. `tools/validate_provenance_docs_vectors.py` checks the records
against their schema without importing this package; this module runs the
real functions.
"""

from __future__ import annotations

import dataclasses
import importlib.resources
import json
from collections.abc import Callable
from datetime import date
from enum import StrEnum

import pytest

from dfo_provenance_docs import (
    DocumentError,
    DocumentErrorReason,
    decode_mcf_text,
    parse_changelog,
    parse_notes,
    parse_prose,
    parse_transformations,
)

_MIN_VECTOR_FLOOR = 60
_SCHEMA_NAME = "provenance-docs-vector.schema.json"
_VECTORS_ROOT = importlib.resources.files("dfo_provenance_docs") / "vectors"

_PARSERS: dict[str, Callable[[str], object]] = {
    "parse_transformations": parse_transformations,
    "parse_changelog": parse_changelog,
    "parse_prose": parse_prose,
    "parse_notes": parse_notes,
}


def escape_markdown(markdown: str) -> str:
    """Verbatim copy of `dfo_pipelines.data_commons.mcf.escape_markdown`."""
    inner = json.dumps(markdown, ensure_ascii=False)[1:-1]
    return '"' + inner.replace('\\"', "\\u0022") + '"'


def _load_vectors() -> list[dict]:
    vectors = [
        json.loads(entry.read_text(encoding="utf-8"))
        for entry in sorted(_VECTORS_ROOT.iterdir(), key=lambda entry: entry.name)
        if entry.name.endswith(".json") and entry.name != _SCHEMA_NAME
    ]
    if not vectors:
        raise RuntimeError("no vectors found under dfo_provenance_docs/vectors")
    return vectors


_VECTORS = _load_vectors()


def _schema() -> dict:
    return json.loads((_VECTORS_ROOT / _SCHEMA_NAME).read_text(encoding="utf-8"))


def _to_json(value: object) -> object:
    """Render a parsed value in the vector suite's JSON shape."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _to_json(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, tuple):
        return [_to_json(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return value


def _run(vector: dict) -> object:
    if vector["function"] == "decode_mcf_text":
        return decode_mcf_text(vector["input"])
    return _PARSERS[vector["function"]](decode_mcf_text(vector["input"]))


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["id"] for v in _VECTORS])
def test_vector(vector: dict) -> None:
    if "error" in vector:
        with pytest.raises(DocumentError) as excinfo:
            _run(vector)
        assert excinfo.value.reason == vector["error"]["reason"]
        assert excinfo.value.line == vector["error"]["line"]
        return
    assert _to_json(_run(vector)) == vector["expect"]


def test_suite_meets_its_floor() -> None:
    assert len(_VECTORS) >= _MIN_VECTOR_FLOOR


def test_every_reason_has_a_rejection_vector() -> None:
    covered = {v["error"]["reason"] for v in _VECTORS if "error" in v}
    assert covered == set(DocumentErrorReason)


def test_every_function_has_an_accepting_vector() -> None:
    covered = {v["function"] for v in _VECTORS if "expect" in v}
    assert covered == {"decode_mcf_text", *_PARSERS}


def test_schema_reason_vocabulary_matches_the_enum() -> None:
    reasons = _schema()["$defs"]["error"]["properties"]["reason"]["enum"]
    assert reasons == [reason.value for reason in DocumentErrorReason]


@pytest.mark.parametrize(
    "vector",
    [v for v in _VECTORS if v["function"] != "decode_mcf_text"],
    ids=[v["id"] for v in _VECTORS if v["function"] != "decode_mcf_text"],
)
def test_parse_vector_input_is_the_pipelines_encoding(vector: dict) -> None:
    """Every document vector stores exactly what the pipelines writer emits."""
    decoded = decode_mcf_text(vector["input"])
    assert escape_markdown(decoded)[1:-1] == vector["input"]
