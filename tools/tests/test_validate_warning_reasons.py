"""Tests for tools/validate_warning_reasons.py: each mutation of the real
registry is one rule the schema, the entry checks or the published-reason
check must refuse.

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
_CODES, _CODE_PROBLEMS = validate_warning_reasons.read_codes(
    validate_warning_reasons.load_json(validate_warning_reasons.CODES_PATH)
)
_CODE = _REGISTRY["entries"][0]["code"]


def _problems(registry: dict[str, Any], codes: dict[str, Any]) -> list[str]:
    return validate_warning_reasons.check_schema(
        registry, _SCHEMA
    ) or validate_warning_reasons.check_entries(registry, codes)


def _with_code(**changes: str) -> dict[str, Any]:
    codes = copy.deepcopy(_CODES)
    codes[_CODE].update(changes)
    return codes


def test_committed_registry_is_valid() -> None:
    assert _CODE_PROBLEMS == []
    assert _problems(_REGISTRY, _CODES) == []
    assert validate_warning_reasons.check_mirror() == []


@pytest.mark.parametrize(
    ("mutate", "codes"),
    [
        pytest.param(lambda r: r["entries"].reverse(), _CODES, id="unsorted"),
        pytest.param(
            lambda r: r["entries"].insert(0, copy.deepcopy(r["entries"][0])),
            _CODES,
            id="duplicate-reason",
        ),
        pytest.param(
            lambda r: None,
            {k: v for k, v in _CODES.items() if k != _CODE},
            id="unregistered-code",
        ),
        pytest.param(
            lambda r: None, _with_code(classification="refusal"), id="refusal-code"
        ),
        pytest.param(
            lambda r: None, _with_code(lifecycle="retired"), id="retired-code"
        ),
        pytest.param(
            lambda r: r["entries"][0].update(reason="Destination_unclassified"),
            _CODES,
            id="upper-case-reason",
        ),
        pytest.param(
            lambda r: r["entries"][0].update(extra=1), _CODES, id="extra-member"
        ),
    ],
)
def test_refuses(
    mutate: Callable[[dict[str, Any]], None], codes: dict[str, Any]
) -> None:
    registry = copy.deepcopy(_REGISTRY)
    mutate(registry)
    assert _problems(registry, codes)


def _changed_since_tag(
    mutate: Callable[[list[dict[str, Any]]], None],
) -> list[str]:
    entries = copy.deepcopy(_REGISTRY["entries"])
    mutate(entries)
    return validate_warning_reasons.check_published(
        entries, _REGISTRY["entries"], "v9.9.9"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda e: e.pop(0), id="removed-reason"),
        pytest.param(
            lambda e: e[0].update(definition="A different meaning."),
            id="redefined-reason",
        ),
        pytest.param(
            lambda e: next(x for x in e if "params" in x)["params"].update(
                other="Another param."
            ),
            id="params-changed",
        ),
    ],
)
def test_refuses_against_tag(mutate: Callable[[list[dict[str, Any]]], None]) -> None:
    assert _changed_since_tag(mutate)


def test_retiring_a_published_reason_passes() -> None:
    assert _changed_since_tag(lambda e: e[0].update(lifecycle="retired")) == []


def test_refuses_reactivating_a_retired_reason() -> None:
    published = copy.deepcopy(_REGISTRY["entries"])
    published[0]["lifecycle"] = "retired"
    assert validate_warning_reasons.check_published(
        _REGISTRY["entries"], published, "v9.9.9"
    )
