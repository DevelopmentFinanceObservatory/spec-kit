"""Drive the schemas and the structure rules against the packaged vector suite.

The suite ships as package data at `dfo_descriptions/vectors/`, so a missing
or emptied directory is a hard failure. The record format is defined in the
README beside the vectors. `tools/validate_descriptions_vectors.py` checks the
records and their schema verdicts without importing this package; this module
also runs `check_steps` and `check_description`.
"""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

from dfo_descriptions import (
    StepsError,
    StepsErrorReason,
    check_description,
    check_steps,
    description_schema,
    steps_schema,
)

_MIN_VECTOR_FLOOR = 40
_RECORD_SCHEMA = "descriptions-vector.schema.json"
_VECTORS_ROOT = importlib.resources.files("dfo_descriptions") / "vectors"

_REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema))
    for schema in (steps_schema(), description_schema())
)
_VALIDATORS = {
    "steps": jsonschema.Draft202012Validator(steps_schema(), registry=_REGISTRY),
    "description": jsonschema.Draft202012Validator(
        description_schema(), registry=_REGISTRY
    ),
}


def _load_vectors() -> list[dict[str, Any]]:
    vectors = [
        json.loads(entry.read_text(encoding="utf-8"))
        for entry in sorted(_VECTORS_ROOT.iterdir(), key=lambda entry: entry.name)
        if entry.name.endswith(".json") and entry.name != _RECORD_SCHEMA
    ]
    if not vectors:
        raise RuntimeError("no vectors found under dfo_descriptions/vectors")
    return vectors


_VECTORS = _load_vectors()


def _check(vector: dict[str, Any]) -> None:
    if vector["schema"] == "steps":
        check_steps(vector["input"])
    else:
        check_description(vector["input"])


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["id"] for v in _VECTORS])
def test_vector(vector: dict[str, Any]) -> None:
    error = vector.get("error", {})
    valid = _VALIDATORS[vector["schema"]].is_valid(vector["input"])
    if error.get("by") == "schema":
        assert not valid
        return
    assert valid

    if error.get("by") == "structure":
        with pytest.raises(StepsError) as excinfo:
            _check(vector)
        assert excinfo.value.reason == error["reason"]
        assert excinfo.value.step == error["step"]
        return
    _check(vector)


def test_suite_meets_its_floor() -> None:
    assert len(_VECTORS) >= _MIN_VECTOR_FLOOR


def test_every_reason_has_a_rejection_vector() -> None:
    covered = {v["error"]["reason"] for v in _VECTORS if "reason" in v.get("error", {})}
    assert covered == set(StepsErrorReason)


def test_record_schema_reason_vocabulary_matches_the_enum() -> None:
    record_schema = json.loads(
        (_VECTORS_ROOT / _RECORD_SCHEMA).read_text(encoding="utf-8")
    )
    structure = record_schema["$defs"]["error"]["oneOf"][1]
    reasons = structure["properties"]["reason"]["enum"]
    assert reasons == [reason.value for reason in StepsErrorReason]
