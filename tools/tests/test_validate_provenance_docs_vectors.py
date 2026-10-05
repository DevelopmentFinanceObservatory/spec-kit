"""Tests for tools/validate_provenance_docs_vectors.py on malformed records.

A record the schema rejects still reaches the checks the schema cannot
express, so those checks must read it without raising, and the validator
must report every diagnostic and exit 1.

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
    Path(__file__).resolve().parent.parent / "validate_provenance_docs_vectors.py"
)
_spec = importlib.util.spec_from_file_location(
    "validate_provenance_docs_vectors", _MODULE_PATH
)
validator = importlib.util.module_from_spec(_spec)
sys.modules["validate_provenance_docs_vectors"] = validator
_spec.loader.exec_module(validator)


@pytest.fixture
def vectors_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the real suite the test may add malformed records to."""
    copy = tmp_path / "vectors"
    shutil.copytree(validator.VECTORS_DIR, copy)
    monkeypatch.setattr(validator, "VECTORS_DIR", copy)
    monkeypatch.setattr(validator, "SCHEMA_PATH", copy / validator.SCHEMA_PATH.name)
    return copy


def _write(directory: Path, record: dict[str, Any]) -> None:
    (directory / f"{record['id']}.json").write_text(json.dumps(record))


def test_the_real_suite_is_valid(vectors_dir: Path) -> None:
    assert validator.validate() == []


@pytest.mark.parametrize(
    "fields",
    [
        {"error": {}},
        {"error": {"reason": ["raw_html"], "line": 1}},
        {"function": ["parse_prose"], "expect": "x"},
    ],
    ids=["empty-error", "unhashable-reason", "unhashable-function"],
)
def test_a_malformed_record_is_reported_without_raising(
    vectors_dir: Path, fields: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    record = {
        "id": "malformed",
        "description": "Malformed.",
        "function": "parse_prose",
        "input": "x",
        **fields,
    }
    _write(vectors_dir, record)

    problems = validator.validate()
    assert problems
    assert all(problem.startswith("malformed.json: schema: ") for problem in problems)
    assert validator.main() == 1
    assert "malformed.json: schema: " in capsys.readouterr().err


def test_a_repeated_unhashable_id_is_reported_without_raising(
    vectors_dir: Path,
) -> None:
    record = {
        "id": ["odd"],
        "description": "Odd.",
        "function": "parse_prose",
        "input": "x",
        "expect": "x",
    }
    for name in ("odd-one", "odd-two"):
        (vectors_dir / f"{name}.json").write_text(json.dumps(record))

    problems = validator.validate()
    for name in ("odd-one", "odd-two"):
        assert any(p.startswith(f"{name}.json: schema: id: ") for p in problems)
        assert f"{name}.json: id ['odd'] does not match the file name" in problems
