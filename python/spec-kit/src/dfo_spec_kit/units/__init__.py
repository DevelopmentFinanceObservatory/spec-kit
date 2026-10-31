"""One typed value per unit ref.

A unit ref is the dcid a facet names as its unit (`CurrencyUSD`, `USDollar`,
`Percent`, `undata/UNIT_MEASURE-CR_USD_K`). `parse_unit` reads one into a
`Money`, `UnpricedMoney`, `Percent`, `Count`, `Rate` or `Other`. Frames keep the
native ref text in their cells, and a reader types it with this module.

The rules come from `registries/units.json` (canonical) through its packaged
mirror `units.json` beside this module:

- A ref that is one of the registry's `currency_prefixes` followed by a
  currency code is money in that currency with prices unstated. After a
  non-empty prefix (`Currency`) a code is any three upper-case ASCII
  letters. A bare ref, the empty prefix, is money only when it is one of the
  registry's `currency_codes`, so `TEU` or `PPM` parses as `Other`.
  `CurrencyPerUSDollar` is not a prefix plus a code, so it parses as
  `Other`.
- A ref the registry declares in `units` parses to the value its row states.
- Any other ref parses as `Other(ref)`, which equals only the same ref. A
  ref that scales its values (`SDG_CU_USD_M`, `SDG_NUM_TH`,
  `PeopleInThousands`) is undeclared, so it parses as `Other`.

Most money refs leave prices unstated, and those parse to `UnpricedMoney`,
which raises `TypeError` on any comparison. `complete_prices` turns one into
a `Money` from the StatVar's measurement qualifier, which the caller reads.
`CurrencyUSD` and `USDollar` are one value once both are completed under the
same qualifier. A constant-price `Money` with no `base_year` raises the same
`TypeError`, because two unknown base years may differ.

Neither incomparable value can go in a set or be a dict key. Each call to
`parse_unit` returns a new object, so `x in [y]` on two parses of one ref
raises too. Only a list holding the very object tested finds it by identity.

Field names agree with the rest of the kit. `currency`, `prices` and
`base_year` are the names a converted frame's unit cell uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from importlib import resources
from typing import Any, Literal

from dfo_spec_kit.currency import is_currency_code

Prices = Literal["current", "constant"]

_PRICES: frozenset[str] = frozenset(("current", "constant"))

# The Data Commons measurement qualifiers that state a price basis.
_QUALIFIER_PRICES: dict[str, Prices] = {
    "Nominal": "current",
    "RealValue": "constant",
}


def _require_currency(currency: object) -> None:
    if not is_currency_code(currency):
        raise ValueError(f"currency must be a currency code: {currency!r}")


@dataclass(frozen=True, slots=True, eq=False)
class Money:
    """An amount of one currency at a stated price basis.

    `prices` is `current` or `constant`. `base_year` is the year constant
    prices are expressed in, `None` when unstated. `chained` is true for a
    chain-linked series, which only constant prices can be, so a chained
    amount never equals a fixed-base amount of the same base year.

    Two values are equal when every field is. A constant-price value with no
    `base_year` cannot be compared, because two unknown base years may
    differ. `==` and `!=` raise `TypeError` with it on either side, and
    `hash` raises `TypeError`, so it cannot go in a set or be a dict key.
    """

    currency: str
    prices: Prices
    base_year: int | None = None
    chained: bool = False

    def __post_init__(self) -> None:
        _require_currency(self.currency)
        # The str check runs first: `in` on a frozenset raises TypeError for
        # an unhashable value.
        if not isinstance(self.prices, str) or self.prices not in _PRICES:
            raise ValueError(f"invalid prices: {self.prices!r}")
        if self.base_year is not None:
            # bool is an int subclass, and True would equal and hash as 1.
            if type(self.base_year) is not int:
                raise ValueError(f"base_year must be an int: {self.base_year!r}")
            if self.prices != "constant":
                raise ValueError("base_year is only valid when prices='constant'")
        if type(self.chained) is not bool:
            raise ValueError(f"chained must be a bool: {self.chained!r}")
        if self.chained and self.prices != "constant":
            raise ValueError("chained is only valid when prices='constant'")

    @property
    def base_year_unknown(self) -> bool:
        """Whether prices are constant in a base year nobody states."""
        return self.prices == "constant" and self.base_year is None

    def _key(self) -> tuple[object, ...]:
        return (self.currency, self.prices, self.base_year, self.chained)

    def _refuse_unknown_base_year(self) -> None:
        if self.base_year_unknown:
            raise TypeError(
                f"money in {self.currency} at constant prices in an unstated "
                "base year cannot be compared"
            )

    def __eq__(self, other: object) -> bool:
        self._refuse_unknown_base_year()
        if not isinstance(other, Money):
            return NotImplemented
        other._refuse_unknown_base_year()
        return self._key() == other._key()

    def __hash__(self) -> int:
        self._refuse_unknown_base_year()
        return hash((Money, *self._key()))

    def as_json(self) -> dict[str, object]:
        """The value as a JSON object, every member present."""
        return {
            "kind": "money",
            "currency": self.currency,
            "prices": self.prices,
            "base_year": self.base_year,
            "chained": self.chained,
        }


@dataclass(frozen=True, slots=True, eq=False)
class UnpricedMoney:
    """An amount of one currency whose ref leaves the price basis unstated.

    Current and constant amounts in one currency are different units, so
    this value cannot be compared with anything. `==` and `!=` raise
    `TypeError`, and the value is unhashable. `complete_prices` gives the
    comparable `Money`.
    """

    currency: str

    def __post_init__(self) -> None:
        _require_currency(self.currency)

    def __eq__(self, other: object) -> bool:
        raise TypeError(
            f"money in {self.currency} with unstated prices cannot be compared; "
            "complete it with complete_prices"
        )

    __hash__ = None

    def as_json(self) -> dict[str, object]:
        """The value as a JSON object, every member present."""
        return {"kind": "unpriced_money", "currency": self.currency}


@dataclass(frozen=True, slots=True)
class Percent:
    """Per cent of the variable's own denominator, so a stored 5 is 5 per cent."""

    def as_json(self) -> dict[str, object]:
        """The value as a JSON object."""
        return {"kind": "percent"}


@dataclass(frozen=True, slots=True)
class Count:
    """A number of things. What is counted is on the variable."""

    def as_json(self) -> dict[str, object]:
        """The value as a JSON object."""
        return {"kind": "count"}


@dataclass(frozen=True, slots=True)
class Rate:
    """A number per a stated count of something, so a stored 12 under a
    `count_base` of 1000 and `counted` of `people` is 12 per 1,000 people.

    `count_base` is that count. `counted` is what it counts, a plural noun
    phrase, `None` when the ref states none. Two refs of one base and one
    counted thing are one value.
    """

    count_base: int
    counted: str | None

    def __post_init__(self) -> None:
        # bool is an int subclass, and True would equal and hash as 1.
        if type(self.count_base) is not int or self.count_base < 2:
            raise ValueError(f"count_base must be an int above 1: {self.count_base!r}")
        if self.counted is not None and (
            not isinstance(self.counted, str) or not self.counted
        ):
            raise ValueError(
                f"counted must be a non-empty string or None: {self.counted!r}"
            )

    def as_json(self) -> dict[str, object]:
        """The value as a JSON object, every member present."""
        return {
            "kind": "rate",
            "count_base": self.count_base,
            "counted": self.counted,
        }


@dataclass(frozen=True, slots=True)
class Other:
    """A ref with no typed reading, equal only to the same ref."""

    ref: str

    def as_json(self) -> dict[str, object]:
        """The value as a JSON object, every member present."""
        return {"kind": "other", "ref": self.ref}


type Unit = Money | UnpricedMoney | Percent | Count | Rate | Other


def _currency_of(
    ref: str, prefixes: tuple[str, ...], codes: frozenset[str]
) -> str | None:
    """The currency a prefix-rule ref names, or `None`. After the empty
    prefix the code must be one of `codes`."""
    for prefix in prefixes:
        code = ref[len(prefix) :]
        if (
            ref.startswith(prefix)
            and is_currency_code(code)
            and (prefix or code in codes)
        ):
            return code
    return None


# The members each kind of `units.json` row carries, every one required.
# Money with `prices` null reads as unpriced money and carries no
# `base_year` or `chained`.
_MONEY_MEMBERS = frozenset(
    ("ref", "kind", "currency", "prices", "base_year", "chained", "evidence")
)
_UNPRICED_MONEY_MEMBERS = frozenset(("ref", "kind", "currency", "prices", "evidence"))
_BARE_MEMBERS = frozenset(("ref", "kind", "evidence"))
_RATE_MEMBERS = frozenset(("ref", "kind", "count_base", "counted", "evidence"))


def _require_members(row: dict[str, Any], members: frozenset[str]) -> None:
    if set(row) != members:
        raise ValueError(
            f"unit row {row.get('ref')!r} of kind {row.get('kind')!r} must carry "
            f"exactly {sorted(members)}, got {sorted(row)}"
        )
    evidence = row["evidence"]
    if not isinstance(evidence, str) or not evidence:
        raise ValueError(
            f"unit row {row.get('ref')!r} evidence must be a non-empty string: "
            f"{evidence!r}"
        )


def _row_to_unit(row: dict[str, Any]) -> Unit:
    match row.get("kind"):
        case "money" if row.get("prices") is None:
            _require_members(row, _UNPRICED_MONEY_MEMBERS)
            return UnpricedMoney(currency=row["currency"])
        case "money":
            _require_members(row, _MONEY_MEMBERS)
            return Money(
                currency=row["currency"],
                prices=row["prices"],
                base_year=row["base_year"],
                chained=row["chained"],
            )
        case "percent":
            _require_members(row, _BARE_MEMBERS)
            return Percent()
        case "count":
            _require_members(row, _BARE_MEMBERS)
            return Count()
        case "rate":
            _require_members(row, _RATE_MEMBERS)
            return Rate(count_base=row["count_base"], counted=row["counted"])
        case kind:
            raise ValueError(f"unit row {row.get('ref')!r} has unknown kind {kind!r}")


def _load_registry() -> tuple[tuple[str, ...], frozenset[str], dict[str, Unit]]:
    """The currency prefixes, currency codes and declared rows of the
    packaged `units.json`."""
    text = (
        resources.files("dfo_spec_kit.units")
        .joinpath("units.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(text)
    if data["format_version"] != 1:
        raise ValueError(
            f"unknown units.json format_version {data['format_version']!r}"
        )
    prefixes = tuple(data["currency_prefixes"])
    codes = frozenset(data["currency_codes"])
    declared: dict[str, Unit] = {}
    for row in data["units"]:
        ref = row["ref"]
        if ref in declared:
            raise ValueError(f"unit ref declared twice: {ref!r}")
        if _currency_of(ref, prefixes, codes) is not None:
            raise ValueError(f"unit ref {ref!r} is declared and matches a prefix rule")
        declared[ref] = _row_to_unit(row)
    return prefixes, codes, declared


_CURRENCY_PREFIXES, _CURRENCY_CODES, _DECLARED = _load_registry()


def parse_unit(ref: str) -> Unit:
    """The typed value a unit ref names.

    Args:
        ref: The unit's dcid, as a facet carries it.

    Returns:
        A new object on every call: `UnpricedMoney` for a currency-prefix
        ref, a copy of the declared value for a registry row, and
        `Other(ref)` for anything else. A shared instance would let
        `x in [y]` match an incomparable value by identity.
    """
    currency = _currency_of(ref, _CURRENCY_PREFIXES, _CURRENCY_CODES)
    if currency is not None:
        return UnpricedMoney(currency=currency)
    declared = _DECLARED.get(ref)
    return replace(declared) if declared is not None else Other(ref=ref)


def complete_prices(unit: Unit, *, measurement_qualifier: str | None) -> Unit:
    """Give unpriced money the price basis the StatVar's qualifier states.

    `Nominal` gives current prices and `RealValue` constant prices in an
    unstated base year. Any other unit, and unpriced money under an unknown
    or absent qualifier, come back unchanged.

    Args:
        unit: A typed unit, usually from `parse_unit`.
        measurement_qualifier: The StatVar's `measurementQualifier` dcid, or
            `None` when it declares none.
    """
    if not isinstance(unit, UnpricedMoney):
        return unit
    prices = _QUALIFIER_PRICES.get(measurement_qualifier or "")
    return unit if prices is None else Money(currency=unit.currency, prices=prices)


__all__ = [
    "Count",
    "Money",
    "Other",
    "Percent",
    "Prices",
    "Rate",
    "Unit",
    "UnpricedMoney",
    "complete_prices",
    "parse_unit",
]
