"""Tests for tools/validate_limit_registry.py: each mutation of the real
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

_MODULE_PATH = Path(__file__).resolve().parent.parent / "validate_limit_registry.py"
_spec = importlib.util.spec_from_file_location("validate_limit_registry", _MODULE_PATH)
validate_limit_registry = importlib.util.module_from_spec(_spec)
sys.modules["validate_limit_registry"] = validate_limit_registry
_spec.loader.exec_module(validate_limit_registry)

_SCHEMA = validate_limit_registry.load_json(validate_limit_registry.SCHEMA_PATH)
_REGISTRY = validate_limit_registry.load_json(validate_limit_registry.REGISTRY_PATH)


def _problems(registry: dict[str, Any]) -> list[str]:
    return validate_limit_registry.check_schema(
        registry, _SCHEMA
    ) or validate_limit_registry.check_entries(registry)


def test_committed_registry_is_valid() -> None:
    assert _problems(_REGISTRY) == []
    assert validate_limit_registry.check_mirror() == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(format_version=2), id="format-version"),
        pytest.param(
            lambda r: r["entries"][0].update(name="max_envelope_bytes"),
            id="lower-case-name",
        ),
        pytest.param(
            lambda r: r["entries"][0].update(owning_contract="billing"),
            id="unknown-contract",
        ),
        pytest.param(lambda r: r["entries"][0].pop("definition"), id="no-definition"),
        pytest.param(lambda r: r["entries"][0].update(extra=1), id="extra-member"),
        pytest.param(lambda r: r["entries"].reverse(), id="unsorted"),
        pytest.param(
            lambda r: r["entries"].insert(0, copy.deepcopy(r["entries"][0])),
            id="duplicate-name",
        ),
    ],
)
def test_refuses(mutate: Callable[[dict[str, Any]], None]) -> None:
    registry = copy.deepcopy(_REGISTRY)
    mutate(registry)
    assert _problems(registry)
