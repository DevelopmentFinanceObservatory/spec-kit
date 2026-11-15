"""Tests for tools/validate_warning_reasons.py: each mutation of the real
registry is one rule the schema or the entry checks must refuse.

Run with: uv run --with jsonschema --with pyarrow --with pytest pytest tools/tests -q
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "validate_warning_reasons.py"
_spec = importlib.util.spec_from_file_location("validate_warning_reasons", _MODULE_PATH)
validate_warning_reasons = importlib.util.module_from_spec(_spec)
sys.modules["validate_warning_reasons"] = validate_warning_reasons
_spec.loader.exec_module(validate_warning_reasons)

_SCHEMA = validate_warning_reasons.load_json(validate_warning_reasons.SCHEMA_PATH)
_REGISTRY = validate_warning_reasons.load_json(validate_warning_reasons.REGISTRY_PATH)
_CODES = validate_warning_reasons.load_json(validate_warning_reasons.CODES_PATH)
_CODE = _REGISTRY["entries"][0]["code"]


def _problems(registry: dict[str, Any], codes: dict[str, Any] = _CODES) -> list[str]:
    return validate_warning_reasons.check_schema(
        registry, _SCHEMA
    ) or validate_warning_reasons.check_entries(registry, codes)


def _codes_with(**changes: str) -> dict[str, Any]:
    codes = copy.deepcopy(_CODES)
    next(e for e in codes["entries"] if e["code"] == _CODE).update(changes)
    return codes


def test_committed_registry_is_valid() -> None:
    assert _problems(_REGISTRY) == []
    assert validate_warning_reasons.check_mirror() == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r["entries"].reverse(), id="unsorted"),
        pytest.param(
            lambda r: r["entries"].insert(0, copy.deepcopy(r["entries"][0])),
            id="duplicate-reason",
        ),
        pytest.param(
            lambda r: r["entries"][0].update(reason="Destination_unclassified"),
            id="upper-case-reason",
        ),
        pytest.param(lambda r: r["entries"][0].update(extra=1), id="extra-member"),
        pytest.param(
            lambda r: r["entries"][0].update(params={"why": "Reserved."}),
            id="why-param",
        ),
        pytest.param(lambda r: r["entries"][0].update(params={}), id="empty-params"),
    ],
)
def test_refuses(mutate: Callable[[dict[str, Any]], None]) -> None:
    registry = copy.deepcopy(_REGISTRY)
    mutate(registry)
    assert _problems(registry)


@pytest.mark.parametrize(
    "codes",
    [
        pytest.param(
            {"entries": [e for e in _CODES["entries"] if e["code"] != _CODE]},
            id="unregistered-code",
        ),
        pytest.param(_codes_with(classification="refusal"), id="refusal-code"),
        pytest.param(_codes_with(lifecycle="retired"), id="retired-code"),
    ],
)
def test_refuses_a_code_that_is_not_an_active_warning(codes: dict[str, Any]) -> None:
    assert _problems(_REGISTRY, codes)
