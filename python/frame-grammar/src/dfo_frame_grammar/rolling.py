"""Relative periods: the period "latest" names over a set of series, and
whole-period offsets from it.

A relative period is `{anchor: "latest", offset: n}` with `n <= 0`.
`latest_anchor` resolves the anchor and `shift_period` applies the offset, so
every service that reads "latest" lands on the same period.

The anchor rule is `ANCHOR_RULE`. A period qualifies in a population when the
series holding it are at least `ANCHOR_SHARE` of that population's series, and
the anchor is the newest period that qualifies in every population. A caller
that hashes a resolved period into an identity hashes `ANCHOR_RULE` beside it,
so a different share is a different rule name and a different identity.

Periods are `time_period` cells. `parse_time_period` decides each label's
grain and whether it is a point, and points at one grain order by their
zero-padded spelling.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import ClassVar, Final

from dfo_frame_grammar.grammar import TimeGrain, TimePeriod, parse_time_period

ANCHOR_RULE: Final = "half1"
ANCHOR_SHARE: Final = Fraction(1, 2)


class RollingError(ValueError):
    """A relative period that cannot be resolved. `code` names the refusal,
    and each subclass is named for its code.
    """

    code: ClassVar[str]


class RollingAnchorUnresolved(RollingError):  # noqa: N818 -- named for its code
    """No period is the anchor: there is no population, a population has no
    series, or no period qualifies in every population.
    """

    code = "rolling_anchor_unresolved"


class RollingGrainUnsupported(RollingError):  # noqa: N818 -- named for its code
    """The periods have no single supported grain: a label is a span, the
    labels mix grains, or a day-grain period is asked to move.
    """

    code = "rolling_grain_unsupported"


@dataclass(frozen=True)
class Anchor[K: Hashable]:
    """The period "latest" resolves to, and the series that do not hold it.

    `period` is a point label at the populations' grain. `left_out` lists
    `(population index, series key)` for every series with no row at `period`,
    in population order and then in each population's own series order.
    """

    period: str
    left_out: tuple[tuple[int, K], ...]


def latest_anchor[K: Hashable](
    populations: Sequence[Mapping[K, Collection[str]]],
) -> Anchor[K]:
    """Resolve "latest" over `populations`.

    Args:
        populations: One mapping per population, from series key to the
            period labels that series holds. A series holding no period
            counts towards its population's size.

    Returns:
        The newest period P such that, in every population, the series
        holding P are at least `ANCHOR_SHARE` of the population's series.

    Raises:
        RollingAnchorUnresolved: `populations` is empty, a population has no
            series, or no period qualifies in every population.
        RollingGrainUnsupported: A label is a span, or the labels mix grains.
        FrameError: `invalid_time_period` for a label `parse_time_period`
            refuses.
    """
    if not populations:
        raise RollingAnchorUnresolved("no population to resolve 'latest' over")

    grains: set[TimeGrain] = set()
    held: list[dict[K, set[str]]] = []
    for population in populations:
        series: dict[K, set[str]] = {}
        for key, labels in population.items():
            points = [_point(label) for label in labels]
            grains.update(point.grain for point in points)
            series[key] = {point.start for point in points}
        held.append(series)
    if len(grains) > 1:
        raise RollingGrainUnsupported(
            f"'latest' needs one time grain, found {', '.join(sorted(grains))}"
        )

    qualifying: set[str] | None = None
    for index, series in enumerate(held):
        if not series:
            raise RollingAnchorUnresolved(f"population {index} has no series")
        holders = Counter(period for periods in series.values() for period in periods)
        passing = {
            period
            for period, count in holders.items()
            if count >= ANCHOR_SHARE * len(series)
        }
        qualifying = passing if qualifying is None else qualifying & passing

    if not qualifying:
        raise RollingAnchorUnresolved(
            f"no period is held by at least {ANCHOR_SHARE} of the series in every "
            "population"
        )

    # Same-grain zero-padded points order by code point as by date.
    period = max(qualifying)
    left_out = tuple(
        (index, key)
        for index, series in enumerate(held)
        for key, periods in series.items()
        if period not in periods
    )
    return Anchor(period=period, left_out=left_out)


def shift_period(period: str, offset: int) -> str:
    """Move `period` by `offset` whole periods at its own grain.

    A year moves by years and a month by months. A day does not move, because
    on day-grain data "N periods back" has no single meaning when the observed
    days need not be consecutive.

    `offset` is an `int`. A JSON `integer` admits `-1.0`, so a caller reading
    an offset from JSON converts an integral float before calling.

    Raises:
        TypeError: `offset` is not an `int`.
        RollingGrainUnsupported: `period` is a span, or it is a day and
            `offset` is not 0.
        FrameError: `invalid_time_period` when `period` is not a `time_period`
            cell, or the move leaves the years `0000` to `9999`.
    """
    if type(offset) is not int:
        raise TypeError(f"offset must be an int, got {type(offset).__name__}")
    point = _point(period)
    if offset == 0:
        return point.start
    if point.grain == "day":
        raise RollingGrainUnsupported(
            f"a day-grain period moves by offset 0 only, got {offset} for {period!r}"
        )
    if point.grain == "year":
        shifted = f"{int(point.start) + offset:04d}"
    else:
        year, month = divmod(_month_index(point.start) + offset, 12)
        shifted = f"{year:04d}-{month + 1:02d}"
    return parse_time_period(shifted).start


def _point(label: str) -> TimePeriod:
    """Parse `label`, refusing a span."""
    parsed = parse_time_period(label)
    if parsed.start != parsed.end:
        raise RollingGrainUnsupported(
            f"a relative period needs point periods, got the span {label!r}"
        )
    return parsed


def _month_index(month: str) -> int:
    """Months since year 0 for a `YYYY-MM` label."""
    year, month_of_year = month.split("-")
    return int(year) * 12 + int(month_of_year) - 1
