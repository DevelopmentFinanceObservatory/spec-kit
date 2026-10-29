"""Replay the analysis and publication rule vectors through a reference
implementation of each rule.

The rules are stated in `dfo_client_contract/rules/README.md`. DFO applies
them on the server; a client reads their results and applies only the
output comparison. Each function here is the shortest faithful reading of one
rule, so a vector that disagrees with its rule fails here before the server
or a client replays it.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import pytest
import rfc8785

from dfo_client_contract import rule

# The frame columns that name no role (docs/frame-grammar-v1.md).
_NOT_ROLES = frozenset(
    {
        "stat_var",
        "time_period",
        "value",
        "unit",
        "measurementMethod",
        "provenance",
        "status",
    }
)
_PERCENT = "Percent"


class RuleError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _vectors(name: str) -> list[dict[str, Any]]:
    return rule(name)["vectors"]


def _ids(name: str) -> list[str]:
    return [vector["name"] for vector in _vectors(name)]


def jcs(value: Any) -> str:
    """The RFC 8785 serialisation of a JSON value."""
    return rfc8785.dumps(value).decode("utf-8")


def _without_recipe(unit: Any) -> Any:
    if not isinstance(unit, dict):
        return unit
    if "numerator" in unit:
        return {side: _without_recipe(part) for side, part in unit.items()}
    return {name: item for name, item in unit.items() if name != "recipe"}


def output_digest(frame: dict[str, Any]) -> tuple[str, str]:
    """The output digest document of a frame, and its token."""
    roles = [column for column in frame["columns"] if column not in _NOT_ROLES]
    columns = ["stat_var", *roles, "time_period", "value", "unit"]
    rows = []
    for row in frame["rows"]:
        cells = dict(zip(frame["columns"], row, strict=True))
        cells["unit"] = _without_recipe(cells["unit"])
        rows.append([cells[column] for column in columns])
    rows.sort(key=lambda cells: jcs(cells).encode("utf-8"))
    document = jcs({"digest_version": "out2", "columns": columns, "rows": rows})
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
    return document, f"out_{digest[:32]}"


def _identity(member: dict[str, Any]) -> tuple[str, ...]:
    spec = member["spec"]
    return (
        spec["spec_id"],
        member["content_version"],
        jcs(member["artifact"]["rendering"]),
        jcs(spec.get("recipe")),
    )


def _output(member: dict[str, Any]) -> tuple[Any, ...]:
    if "output_digest" in member:
        return ("derived", member["output_digest"])
    rendering = member["artifact"]["rendering"]
    cut = rendering.get("without_party_totals", False)
    return ("dataset", member["content_version"], cut)


def refs_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return [_identity(m) for m in left["members"]] == [
        _identity(m) for m in right["members"]
    ]


def same_data(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return [_output(m) for m in left["members"]] == [
        _output(m) for m in right["members"]
    ]


def _paired(
    data: list[dict[str, Any]], other: list[dict[str, Any]], on: dict[str, str]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Each data row with the other row at its paired keys and period."""
    pairs = []
    for row in data:
        found = [
            match
            for match in other
            if match["time_period"] == row["time_period"]
            and all(row[mine] == match[theirs] for mine, theirs in on.items())
        ]
        assert len(found) <= 1, row
        pairs.extend((row, match) for match in found)
    return pairs


def _growth(vector: dict[str, Any]) -> list[dict[str, Any]]:
    """Each member's change over the window. Two rows of one member in one
    period of the window refuse with `role_ambiguous`. A member is left out
    without a value at both ends (`endpoint_missing`), for a relative change
    with a zero or sign-crossing base (`zero_or_sign_crossing_base`), and
    for a change that is not finite (`non_finite`)."""
    arguments = vector["arguments"]
    start, end = arguments["window"]
    by = arguments["by"]
    relative = arguments.get("change", "relative") == "relative"
    groups: dict[tuple[str, ...], dict[str, dict[str, Any]]] = {}
    for row in vector["data"]:
        period = row["time_period"]
        if not start <= period <= end:
            continue
        periods = groups.setdefault(tuple(row[r] for r in by), {})
        if period in periods:
            raise RuleError("role_ambiguous")
        periods[period] = row
    rows = []
    for key, periods in groups.items():
        if start not in periods or end not in periods:
            continue
        first, last = periods[start], periods[end]
        base, final = first["value"], last["value"]
        if relative and (base == 0 or final == 0 or (base < 0) != (final < 0)):
            continue
        change = (final - base) / base if relative else final - base
        if not math.isfinite(change):
            continue
        rows.append(
            {
                "stat_var": first["stat_var"],
                **dict(zip(by, key, strict=True)),
                "time_period": f"{start}/{end}",
                "value": change,
                "unit": None if relative else first["unit"],
            }
        )
    return rows


def _share_across(vector: dict[str, Any]) -> list[dict[str, Any]]:
    by = vector["arguments"]["by"]
    totals: dict[str, list[float]] = {}
    sums: dict[tuple[Any, ...], list[float]] = {}
    for row in vector["data"]:
        totals.setdefault(row["time_period"], []).append(row["value"])
        key = (*(row[r] for r in by), row["time_period"])
        sums.setdefault(key, []).append(row["value"])
    return [
        {
            **dict(zip(by, key[:-1], strict=True)),
            "time_period": key[-1],
            "value": math.fsum(values) / math.fsum(totals[key[-1]]) * 100.0,
            "unit": _PERCENT,
        }
        for key, values in sums.items()
    ]


def operation_output(vector: dict[str, Any]) -> list[dict[str, Any]]:
    verb, arguments = vector["verb"], vector["arguments"]
    if verb == "growth":
        return _growth(vector)
    if verb == "share_of" and "other" not in vector:
        return _share_across(vector)
    on = arguments["on"]
    rows = []
    for row, match in _paired(vector["data"], vector["other"], on):
        keys = {mine: row[mine] for mine in on}
        period = row["time_period"]
        if verb == "join":
            rows.extend(
                {
                    "stat_var": side["stat_var"],
                    **keys,
                    "time_period": period,
                    "value": side["value"],
                    "unit": side["unit"],
                }
                for side in (row, match)
            )
            continue
        value, unit = {
            "share_of": (row["value"] / match["value"] * 100.0, _PERCENT),
            "compare": (row["value"] - match["value"], row["unit"]),
            "sum_of": (row["value"] + match["value"], row["unit"]),
        }[verb]
        rows.append({**keys, "time_period": period, "value": value, "unit": unit})
    return rows


def _population(population: dict[str, Any]) -> str:
    """A population in its one spelling: each binding's values sorted, once."""
    bindings = {
        column: sorted(set(values))
        for column, values in population.get("bindings", {}).items()
    }
    return jcs({"spec_id": population["spec_id"], "bindings": bindings})


def rolling_anchor(vector: dict[str, Any]) -> str:
    stated = {
        jcs(sorted({_population(p) for p in step["population"]}))
        for step in vector["steps"]
    }
    if len(stated) > 1:
        raise RuleError("rolling_populations_differ")
    held = []
    for population in vector["steps"][0]["population"]:
        bindings = population.get("bindings", {})
        series = [
            set(item["periods"])
            for item in vector["series"]
            if item["spec_id"] == population["spec_id"]
            and all(item["coordinates"].get(c) in v for c, v in bindings.items())
        ]
        if not series:
            raise RuleError("rolling_anchor_unresolved")
        held.append(series)
    periods = {period for series in held for periods in series for period in periods}
    for period in sorted(periods, reverse=True):
        if all(2 * sum(period in s for s in series) >= len(series) for series in held):
            return period
    raise RuleError("rolling_anchor_unresolved")


def _slots(cards: list[str], held: list[str | None]) -> list[str | None]:
    count = max(len(cards), len(held))
    return [cards[i] if i < len(cards) else None for i in range(count)]


def _check_token(share: dict[str, Any], step: dict[str, Any]) -> None:
    token = step.get("write_token")
    if token is None:
        raise RuleError("write_token_required")
    if token != "held" or not share["has_token"]:
        raise RuleError("write_token_invalid")


def _state(share: dict[str, Any]) -> dict[str, Any]:
    return {
        "latest": share["latest"],
        "slots": [
            {"slot": number, "card": card}
            for number, card in enumerate(share["slots"], start=1)
        ],
    }


def publish(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shares: dict[str, dict[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    for step in steps:
        op = step["op"]
        if op == "share":
            share = shares[step["share"]] = {
                "latest": step["latest"],
                "has_token": not step.get("tokenless", False),
                "slots": _slots(step["cards"], []),
            }
            outcomes.append({"share": _state(share)})
            continue
        share = shares[step["share"]]
        try:
            if op in ("replace", "unshare"):
                _check_token(share, step)
            if op == "replace":
                if not share["latest"]:
                    raise RuleError("share_not_replaceable")
                share["slots"] = _slots(step["cards"], share["slots"])
            if op == "unshare":
                del shares[step["share"]]
                outcomes.append({"removed": True})
                continue
        except RuleError as error:
            outcomes.append({"error": error.code})
            continue
        outcomes.append({"share": _state(share)})
    return outcomes


def _rows(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(jcs(row) for row in rows)


@pytest.mark.parametrize("vector", _vectors("output-digest"), ids=_ids("output-digest"))
def test_output_digest(vector: dict[str, Any]) -> None:
    assert output_digest(vector["frame"]) == (vector["document"], vector["digest"])


@pytest.mark.parametrize(
    "vector", _vectors("output-comparison"), ids=_ids("output-comparison")
)
def test_output_comparison(vector: dict[str, Any]) -> None:
    left, right = vector["left"], vector["right"]
    assert refs_equal(left, right) is vector["equal"]
    assert same_data(left, right) is vector["same_data"]
    assert refs_equal(right, left) is vector["equal"]
    assert same_data(right, left) is vector["same_data"]


@pytest.mark.parametrize(
    "vector", _vectors("operation-outputs"), ids=_ids("operation-outputs")
)
def test_operation_outputs(vector: dict[str, Any]) -> None:
    if "error" in vector:
        with pytest.raises(RuleError) as excinfo:
            operation_output(vector)
        assert excinfo.value.code == vector["error"]
        return
    assert _rows(operation_output(vector)) == _rows(vector["rows"])


@pytest.mark.parametrize(
    "vector", _vectors("rolling-composition"), ids=_ids("rolling-composition")
)
def test_rolling_composition(vector: dict[str, Any]) -> None:
    if "error" in vector:
        with pytest.raises(RuleError) as excinfo:
            rolling_anchor(vector)
        assert excinfo.value.code == vector["error"]
        return
    assert rolling_anchor(vector) == vector["period"]


@pytest.mark.parametrize("vector", _vectors("publication"), ids=_ids("publication"))
def test_publication(vector: dict[str, Any]) -> None:
    assert publish(vector["steps"]) == vector["outcomes"]
