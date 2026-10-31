"""Tests for tools/validate_unit_registry.py: each mutation of the real
registry is one rule the schema or the row checks must refuse.

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

_MODULE_PATH = Path(__file__).resolve().parent.parent / "validate_unit_registry.py"
_spec = importlib.util.spec_from_file_location("validate_unit_registry", _MODULE_PATH)
validate_unit_registry = importlib.util.module_from_spec(_spec)
sys.modules["validate_unit_registry"] = validate_unit_registry
_spec.loader.exec_module(validate_unit_registry)

_SCHEMA = validate_unit_registry.load_json(validate_unit_registry.SCHEMA_PATH)
_REGISTRY = validate_unit_registry.load_json(validate_unit_registry.REGISTRY_PATH)


def _problems(registry: dict[str, Any]) -> list[str]:
    return validate_unit_registry.check_schema(
        registry, _SCHEMA
    ) or validate_unit_registry.check_rows(registry)


def _row(registry: dict[str, Any], ref: str) -> dict[str, Any]:
    return next(row for row in registry["units"] if row["ref"] == ref)


def test_committed_registry_is_valid() -> None:
    assert _problems(_REGISTRY) == []
    assert validate_unit_registry.check_mirror() == []


def _set(ref: str, **members: object) -> Callable[[dict[str, Any]], None]:
    return lambda registry: _row(registry, ref).update(members)


def _add(row: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    def mutate(registry: dict[str, Any]) -> None:
        registry["units"].append(row)
        registry["units"].sort(key=lambda r: r["ref"])

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.update(format_version=2), id="format-version"),
        pytest.param(_set("Percent", currency="USD"), id="percent-extra-member"),
        pytest.param(_set("SDG_NUMBER", prices=None), id="count-extra-member"),
        pytest.param(_set("SDG_CU_USD", method=None), id="money-retired-member"),
        pytest.param(_set("Per1000Persons", count_base=1), id="rate-base-of-one"),
        pytest.param(_set("Per1000Persons", count_base="1000"), id="string-base"),
        pytest.param(_set("Per1000Persons", counted=""), id="rate-empty-counted"),
        pytest.param(_set("Percent", count_base=100), id="percent-carries-base"),
        pytest.param(_set("USDollar", chained=False), id="unpriced-carries-chained"),
        pytest.param(_set("SDG_CU_USD", base_year=2015), id="current-base-year"),
        pytest.param(_set("SDG_CU_USD", chained=True), id="current-chained"),
        pytest.param(_set("SDG_CON_USD", base_year="2015"), id="string-base-year"),
        pytest.param(lambda r: r["units"].reverse(), id="unsorted-rows"),
        pytest.param(lambda r: r["currency_codes"].reverse(), id="unsorted-codes"),
        pytest.param(
            lambda r: r["units"].insert(0, copy.deepcopy(r["units"][0])),
            id="duplicate-ref",
        ),
        pytest.param(
            _add({"ref": "EUR", "kind": "percent", "evidence": "x"}),
            id="row-matches-bare-code",
        ),
        pytest.param(
            _add({"ref": "CurrencyTEU", "kind": "count", "evidence": "x"}),
            id="row-matches-currency-prefix",
        ),
    ],
)
def test_refuses(mutate: Callable[[dict[str, Any]], None]) -> None:
    registry = copy.deepcopy(_REGISTRY)
    mutate(registry)
    assert _problems(registry)


def test_bare_ref_outside_currency_codes_may_be_declared() -> None:
    registry = copy.deepcopy(_REGISTRY)
    _add({"ref": "TEU", "kind": "count", "evidence": "x"})(registry)
    assert _problems(registry) == []
