"""Tests for dfo_spec_kit.units: the unit vector suite, the registry mirror,
and the typed values' own invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dfo_spec_kit.currency import is_currency_code
from dfo_spec_kit.units import (
    Count,
    Money,
    Other,
    Percent,
    Rate,
    Unit,
    UnpricedMoney,
    _row_to_unit,
    complete_prices,
    parse_unit,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = _REPO_ROOT / "registries" / "units.json"
_MIRROR_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dfo_spec_kit"
    / "units"
    / "units.json"
)
_VECTOR_PATHS = sorted((_REPO_ROOT / "vectors" / "units").glob("*.json"))
_VECTORS = [json.loads(path.read_text(encoding="utf-8")) for path in _VECTOR_PATHS]


def test_mirror_is_byte_identical_to_canonical_registry() -> None:
    assert _MIRROR_PATH.read_bytes() == _CANONICAL_PATH.read_bytes()


def test_suite_covers_every_classification() -> None:
    assert {vector["classification"] for vector in _VECTORS} == {
        "parse",
        "compare",
        "complete",
    }


def _outcome(first: Unit, second: Unit) -> str:
    try:
        equal = first == second
    except TypeError:
        return "incomparable"
    return "equal" if equal else "different"


@pytest.mark.parametrize("vector", _VECTORS, ids=[path.stem for path in _VECTOR_PATHS])
def test_vector(vector: dict) -> None:
    match vector["classification"]:
        case "parse":
            assert parse_unit(vector["ref"]).as_json() == vector["expected"]
        case "compare":
            first, second = (
                complete_prices(
                    parse_unit(ref),
                    measurement_qualifier=vector["measurement_qualifier"],
                )
                for ref in vector["refs"]
            )
            assert _outcome(first, second) == vector["outcome"]
        case "complete":
            completed = complete_prices(
                parse_unit(vector["ref"]),
                measurement_qualifier=vector["measurement_qualifier"],
            )
            assert completed.as_json() == vector["expected"]


def test_every_registry_row_parses_to_its_declared_kind() -> None:
    rows = json.loads(_CANONICAL_PATH.read_text(encoding="utf-8"))["units"]
    for row in rows:
        unit = parse_unit(row["ref"])
        match row["kind"]:
            case "money" if row["prices"] is None:
                assert isinstance(unit, UnpricedMoney), row["ref"]
            case "money":
                assert isinstance(unit, Money), row["ref"]
            case "percent":
                assert isinstance(unit, Percent), row["ref"]
            case "count":
                assert isinstance(unit, Count), row["ref"]
            case "rate":
                assert isinstance(unit, Rate), row["ref"]
            case _:
                pytest.fail(row["ref"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("USD", True),
        ("ZWG", True),
        ("usd", False),
        ("USDX", False),
        ("US", False),
        ("USD\n", False),
        ("ÄBC", False),
        (None, False),
    ],
)
def test_is_currency_code(value: object, expected: bool) -> None:
    assert is_currency_code(value) is expected


def test_chained_never_equals_fixed_base_of_the_same_year() -> None:
    chained = parse_unit("ChainedUSDollarBaseYr2017")
    assert chained == Money(
        currency="USD", prices="constant", base_year=2017, chained=True
    )
    assert chained != Money(currency="USD", prices="constant", base_year=2017)


def test_other_never_equals_a_typed_unit() -> None:
    usd = complete_prices(parse_unit("USD"), measurement_qualifier="Nominal")
    assert Other(ref="USD") != usd


# Unpriced money, and constant-price money in an unstated base year.
_INCOMPARABLE_REFS = ["CurrencyUSD", "undata/UNIT_MEASURE-CR_USD_K"]


@pytest.mark.parametrize("ref", _INCOMPARABLE_REFS)
@pytest.mark.parametrize(
    "other",
    [
        UnpricedMoney(currency="USD"),
        Money(currency="USD", prices="current"),
        Money(currency="USD", prices="constant"),
        Money(currency="USD", prices="constant", base_year=2015),
        Other(ref="USD"),
        None,
    ],
)
def test_incomparable_money_refuses_every_comparison(ref: str, other: object) -> None:
    incomparable = parse_unit(ref)
    with pytest.raises(TypeError):
        _ = incomparable == other
    with pytest.raises(TypeError):
        _ = other == incomparable
    with pytest.raises(TypeError):
        _ = incomparable != other


@pytest.mark.parametrize(
    "ref", [*_INCOMPARABLE_REFS, "USDollar", "undata/UNIT_MEASURE-CR_USD"]
)
def test_incomparable_money_fails_in_every_container(ref: str) -> None:
    first, second = parse_unit(ref), parse_unit(ref)
    with pytest.raises(TypeError):
        hash(first)
    with pytest.raises(TypeError):
        _ = {first, second}
    with pytest.raises(TypeError):
        _ = first in [second]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"currency": "usd", "prices": "current"},
        {"currency": "USD", "prices": "real"},
        {"currency": "USD", "prices": "current", "base_year": 2015},
        {"currency": "USD", "prices": None},
        {"currency": "USD", "prices": ["current"]},
        {"currency": "USD", "prices": {}},
        {"currency": "USD", "prices": "constant", "base_year": "2015"},
        {"currency": "USD", "prices": "constant", "base_year": True},
        {"currency": "USD", "prices": "constant", "base_year": 2015.0},
        {"currency": "USD", "prices": "constant", "base_year": [2015]},
        {"currency": "USD", "prices": "constant", "base_year": 2015, "chained": 1},
        {"currency": "USD", "prices": "current", "chained": True},
    ],
)
def test_money_refuses_an_invalid_value(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        Money(**kwargs)


_MONEY_ROW = {
    "ref": "test/money",
    "kind": "money",
    "currency": "USD",
    "prices": "constant",
    "base_year": 2015,
    "chained": False,
    "evidence": "x",
}


@pytest.mark.parametrize(
    "row",
    [
        {"ref": "test/percent", "kind": "percent", "currency": "USD", "evidence": "x"},
        {"ref": "test/count", "kind": "count", "prices": None, "evidence": "x"},
        {"ref": "test/count", "kind": "count"},
        {**_MONEY_ROW, "method": None},
        {k: v for k, v in _MONEY_ROW.items() if k != "chained"},
        {**_MONEY_ROW, "prices": None},
        {**_MONEY_ROW, "kind": "ratio"},
        {
            "ref": "test/unpriced",
            "kind": "unpriced_money",
            "currency": "USD",
            "prices": None,
            "evidence": "x",
        },
        {
            "ref": "test/unpriced",
            "kind": "unpriced_money",
            "currency": "USD",
            "prices": "current",
            "evidence": "x",
        },
        {**_MONEY_ROW, "evidence": 5},
        {**_MONEY_ROW, "evidence": ""},
        {"ref": "test/count", "kind": "count", "evidence": ""},
        {"ref": "test/rate", "kind": "rate", "count_base": 1000, "evidence": "x"},
        {
            "ref": "test/rate",
            "kind": "rate",
            "count_base": 1,
            "counted": "people",
            "evidence": "x",
        },
        {
            "ref": "test/rate",
            "kind": "rate",
            "count_base": True,
            "counted": "people",
            "evidence": "x",
        },
        {
            "ref": "test/rate",
            "kind": "rate",
            "count_base": 1000,
            "counted": "",
            "evidence": "x",
        },
    ],
)
def test_registry_row_refuses_members_its_kind_lacks(row: dict) -> None:
    with pytest.raises(ValueError):
        _row_to_unit(row)
