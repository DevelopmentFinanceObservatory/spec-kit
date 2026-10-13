"""Tests for tools/validate_descriptions_vectors.py.

A record the record schema rejects still reaches the checks it cannot
express, so those checks must read it without raising. A vector whose input
does not get the schema verdict it states, and an operation list that breaks
a rule the operation schema cannot express, must each be reported.

Run with: uv run --with jsonschema --with pytest pytest tools/tests -q
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "validate_descriptions_vectors.py"
)
_spec = importlib.util.spec_from_file_location(
    "validate_descriptions_vectors", _MODULE_PATH
)
validator = importlib.util.module_from_spec(_spec)
sys.modules["validate_descriptions_vectors"] = validator
_spec.loader.exec_module(validator)


@pytest.fixture
def package_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the real schemas and suite the test may break."""
    copy = tmp_path / "dfo_descriptions"
    for name in ("schemas", "vectors"):
        shutil.copytree(validator.PACKAGE_DIR / name, copy / name)
    monkeypatch.setattr(validator, "PACKAGE_DIR", copy)
    monkeypatch.setattr(validator, "SCHEMAS_DIR", copy / "schemas")
    monkeypatch.setattr(validator, "VECTORS_DIR", copy / "vectors")
    monkeypatch.setattr(
        validator, "SCHEMA_PATH", copy / "vectors" / validator.SCHEMA_PATH.name
    )
    return copy


def _write(directory: Path, record: dict[str, Any]) -> None:
    (directory / f"{record['id']}.json").write_text(json.dumps(record))


def test_the_real_suite_is_valid(package_dir: Path) -> None:
    assert validator.validate() == []


@pytest.mark.parametrize(
    "fields",
    [
        {"error": {}},
        {"error": {"by": "structure", "reason": ["unknown_input"], "step": 0}},
        {"schema": ["steps"], "expect": "accept"},
    ],
    ids=["empty-error", "unhashable-reason", "unhashable-schema"],
)
def test_a_malformed_record_is_reported_without_raising(
    package_dir: Path, fields: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    record = {
        "id": "malformed",
        "description": "Malformed.",
        "schema": "steps",
        "input": [],
        **fields,
    }
    _write(package_dir / "vectors", record)

    problems = validator.validate()
    assert problems
    assert all(problem.startswith("malformed.json: schema: ") for problem in problems)
    assert validator.main() == 1
    assert "malformed.json: schema: " in capsys.readouterr().err


@pytest.mark.parametrize(
    ("input_", "verdict", "problem"),
    [
        (
            [{"id": "s1"}],
            {"expect": "accept"},
            "wrong-verdict.json: input fails steps at 0: 'op' is a required property",
        ),
        (
            [],
            {"error": {"by": "schema"}},
            "wrong-verdict.json: input validates against steps but the vector "
            "expects the schema to reject it",
        ),
        (
            [{"id": "s1"}],
            {"error": {"by": "structure", "reason": "unknown_input", "step": 0}},
            "wrong-verdict.json: input fails steps at 0: 'op' is a required property",
        ),
    ],
    ids=["accepted-but-invalid", "rejected-but-valid", "structure-but-invalid"],
)
def test_a_wrong_schema_verdict_is_reported(
    package_dir: Path, input_: Any, verdict: dict[str, Any], problem: str
) -> None:
    record = {
        "id": "wrong-verdict",
        "description": "Wrong.",
        "schema": "steps",
        "input": input_,
        **verdict,
    }
    _write(package_dir / "vectors", record)

    assert validator.validate() == [problem]


def test_an_operation_listed_twice_is_reported(package_dir: Path) -> None:
    path = package_dir / "schemas" / "operations.json"
    document = json.loads(path.read_text())
    document["operations"].append(document["operations"][1])
    path.write_text(json.dumps(document))

    assert validator.validate() == ["operations.json: op 'sum' is listed twice"]


def test_a_missing_analysis_operation_is_reported(package_dir: Path) -> None:
    path = package_dir / "schemas" / "operations.json"
    document = json.loads(path.read_text())
    document["operations"] = [
        entry for entry in document["operations"] if entry["op"] != "join"
    ]
    path.write_text(json.dumps(document))

    problems = validator.validate()
    assert len(problems) == 1
    assert problems[0].startswith("operations.json: analysis ops ")
