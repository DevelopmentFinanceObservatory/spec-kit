"""Tests for tools/validate_flow_role_axes.py: each mutation of the real
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

_MODULE_PATH = Path(__file__).resolve().parent.parent / "validate_flow_role_axes.py"
_spec = importlib.util.spec_from_file_location("validate_flow_role_axes", _MODULE_PATH)
validate_flow_role_axes = importlib.util.module_from_spec(_spec)
sys.modules["validate_flow_role_axes"] = validate_flow_role_axes
_spec.loader.exec_module(validate_flow_role_axes)

_SCHEMA = validate_flow_role_axes.load_json(validate_flow_role_axes.SCHEMA_PATH)
_REGISTRY = validate_flow_role_axes.load_json(validate_flow_role_axes.REGISTRY_PATH)


def _problems(registry: dict[str, Any]) -> list[str]:
    return validate_flow_role_axes.check_schema(
        registry, _SCHEMA
    ) or validate_flow_role_axes.check_entries(registry)


def test_committed_registry_is_valid() -> None:
    assert _problems(_REGISTRY) == []
    assert validate_flow_role_axes.check_mirror() == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(format_version=2), id="format-version"),
        pytest.param(
            lambda r: r["entries"][0].update(role="Creditor"),
            id="upper-case-role",
        ),
        pytest.param(
            lambda r: r["entries"][0].update(axis="middle"),
            id="unknown-axis",
        ),
        pytest.param(lambda r: r["entries"][0].pop("axis"), id="no-axis"),
        pytest.param(lambda r: r["entries"][0].update(extra=1), id="extra-member"),
        pytest.param(lambda r: r["entries"].reverse(), id="unsorted"),
        pytest.param(
            lambda r: r["entries"].insert(0, copy.deepcopy(r["entries"][0])),
            id="duplicate-role",
        ),
    ],
)
def test_refuses(mutate: Callable[[dict[str, Any]], None]) -> None:
    registry = copy.deepcopy(_REGISTRY)
    mutate(registry)
    assert _problems(registry)
