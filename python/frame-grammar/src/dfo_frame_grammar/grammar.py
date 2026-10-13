"""Column order, cell domains, and the row-sort collation for a materialised
frame.

Normative source: `docs/frame-grammar-v1.md`. A frame is long-form, one row
per observation. Columns carry the grain: the resolved StatVar, one column
per role the shape declares, the time period, the value, the unit, and
per-row facet identity, plus one column reserved for a per-observation
status and qualification slot that ships empty until a later minor grammar
version populates it -- reserving it from launch means that when it does
populate, the column set does not change, so no `content_version` rotates
for a schema change alone. This module is the executable form of those
rules. A golden vector suite conformance-tests it; where the two disagree,
the vectors win.

Every ordering and string-comparison rule here uses one collation: the
Unicode code point order of the NFC normal form, the same discipline
`dfo_spec_kit.identity.core`'s own canonicalisation pipeline applies, used
here for a different domain -- frame bytes, not spec identity -- under its
own domain separator, `frame_version`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from dfo_spec_kit.currency import is_currency_code
from dfo_spec_kit.identity import canonicalize

STAT_VAR_COLUMN = "stat_var"
TIME_PERIOD_COLUMN = "time_period"
VALUE_COLUMN = "value"
UNIT_COLUMN = "unit"
STATUS_COLUMN = "status"

# The closed facet axis vocabulary, minus `unit`: the unit axis's own facet
# identity is the `unit` column itself, so it never gets a facet-identity
# column of its own (see `derive_columns`). Sorted so the pinned order
# (measurementMethod before provenance) is a consequence of the collation
# rule below, not a hand-maintained assumption that could drift silently if
# the vocabulary's spelling ever changed.
FACET_IDENTITY_COLUMNS: tuple[str, ...] = tuple(
    sorted(("measurementMethod", "provenance"))
)

# Every column name this grammar reserves for a fixed position: a declared
# role name colliding with one of these would silently produce a duplicate
# column (two positions sharing one name), which any `dict(zip(columns,
# row))` consumer would then collapse into one -- refused in
# `normalize_role_keys` rather than left for a downstream consumer to
# discover.
_RESERVED_COLUMN_NAMES: frozenset[str] = frozenset(
    (STAT_VAR_COLUMN, TIME_PERIOD_COLUMN, VALUE_COLUMN, UNIT_COLUMN, STATUS_COLUMN)
) | frozenset(FACET_IDENTITY_COLUMNS)

# The domain separator hashed into canonical frame bytes (see
# `frame.canonical_frame_bytes`). Deliberately not `FRAME_GRAMMAR_VERSION`,
# the published grammar document's semver: a document-only revision, or an
# additive minor bump, must not rotate a single `content_version` on its
# own, and keeping the two tokens separate is what makes that true by
# construction rather than by discipline.
FRAME_VERSION = "frame1"

# The one canonical spelling `stat_var` accepts: a `dcid:`-prefixed
# reference. An unprefixed dcid or a display label names the same
# underlying variable under a different spelling, and accepting either
# would let two frames over one StatVar mint two different sets of
# canonical bytes -- refused in `validate_stat_var_ref` rather than
# silently canonicalised, per the grammar's fail-closed discipline.
STAT_VAR_REF_PREFIX = "dcid:"

# The members of a converted `unit` cell. The identity's unit request
# (`dfo_spec_kit.identity`) is the three members `currency`, `prices` and
# `base_year`. A cell adds `recipe`, the conversion recipe id. A spec asks
# for a unit, so the identity carries no recipe.
_UNIT_CELL_KEYS = frozenset({"currency", "prices", "base_year", "recipe"})
_UNIT_REQUEST_PRICES = frozenset({"current", "constant"})

# The closed member sets of a composed unit and of its StatVar part. An
# object carrying either composed member is read as a composed unit, so a
# unit request with a stray `numerator` refuses as a malformed composed unit
# and a composed unit never reaches the unit-request rules.
_COMPOSED_UNIT_KEYS = frozenset({"numerator", "denominator"})
_STAT_VAR_PART_KEYS = frozenset({"stat_var"})

# The largest integer magnitude JCS (RFC 8785) can serialise: the
# ECMAScript safe-integer bound, 2**53 - 1. `rfc8785` refuses anything
# beyond it with its own exception type from deep inside serialisation, so
# `validate_unit_request` refuses it up front instead -- every unit request
# this module accepts must be one `canonicalize` can actually serialise,
# and every refusal must carry this package's own stable category.
_JCS_MAX_SAFE_INTEGER = 2**53 - 1

# One period point: YYYY, YYYY-MM, or YYYY-MM-DD, zero-padded, with a real
# calendar month (01-12) and a plausible day (01-31). Deliberately does not
# reject a day past its own month's length (e.g. 30 February): the frame
# carries a source's own declared time period verbatim, at whatever
# granularity the source published, rather than re-deriving calendar
# validity the source itself is the authority on. `re.ASCII` pins `\d` to
# the ASCII digits 0-9: without it, `\d` matches any Unicode decimal digit,
# and NFC normalisation does not fold other scripts' digits to ASCII, so a
# noncanonical spelling of one period would otherwise enter canonical bytes
# and mint its own distinct token.
_PERIOD_POINT = r"\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?"

# A `time_period` cell: one point, or an ISO 8601 interval of two points
# joined by `/`. Every other interval spelling (an open end, a duration, the
# `--` separator) fails this pattern.
_TIME_PERIOD_RE = re.compile(
    rf"(?P<start>{_PERIOD_POINT})(?:/(?P<end>{_PERIOD_POINT}))?", re.ASCII
)

TimeGrain = Literal["year", "month", "day"]

_GRAIN_BY_LENGTH: dict[int, TimeGrain] = {
    len("YYYY"): "year",
    len("YYYY-MM"): "month",
    len("YYYY-MM-DD"): "day",
}


@dataclass(frozen=True, slots=True)
class TimePeriod:
    """A parsed `time_period` cell. A point has `start == end`; an interval
    has `start < end`, both at `grain`.
    """

    start: str
    end: str
    grain: TimeGrain


class FrameError(ValueError):
    """A refusal at some stage of frame construction or canonicalisation.

    ``category`` names the kind of refusal: ``lone_surrogate``,
    ``non_finite_number``, ``invalid_time_period``, ``invalid_unit`` (a
    unit-request object or a composed unit outside its closed shape),
    ``duplicate_row_key``, or ``schema_violation`` for a malformed input
    this package has no more specific name for. ``column_mismatch`` and
    ``row_key_not_increasing`` are raised only by
    `dfo_frame_grammar.frame.stream_canonical_frame_bytes`: a later chunk
    whose `columns` differs from the stream's first chunk, or a row whose
    `row_sort_key` is not strictly greater than the previous row's (equal
    keys included), checked both within a chunk and across a chunk
    boundary. Kept stable so a caller can match on failure kind, the same
    way `dfo_spec_kit.identity`'s ``IdentityError.category`` does for the
    identity pipeline.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def reject_lone_surrogates(text: str) -> None:
    """Refuse `text` if it contains an unpaired UTF-16 surrogate code point.

    Every string cell and every column name is NFC-normalised and checked
    here at construction; an unpaired surrogate cannot round-trip through
    UTF-8, so it can never survive to `canonical_frame_bytes` -- refusing
    it early, at the same point normalisation happens, is what makes that
    true rather than an accident of what `rfc8785` happens to accept.
    """
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        raise FrameError(
            "lone_surrogate", "string contains an unpaired UTF-16 surrogate"
        )


def normalize_role_keys(role_keys: Iterable[str]) -> tuple[str, ...]:
    """NFC-normalise, de-duplicate, and sort `role_keys` by Unicode code
    point of the NFC form -- the platform's one collation.

    Refuses if two distinct raw keys collide after NFC normalisation:
    silently merging them into one column would drop one role's entity
    dcids with no trace, the same failure mode the identity projection's
    own entity-role handling refuses rather than masks.

    Also refuses a role key equal to any reserved column name (`stat_var`,
    `time_period`, `value`, `unit`, `measurementMethod`, `provenance`,
    `status`): a role sharing one of those names would derive a `columns`
    array with that name in two positions, which any consumer that pairs
    cells with column names by name (rather than by position) would then
    collapse into one.
    """
    seen: dict[str, str] = {}
    for raw_key in role_keys:
        if not isinstance(raw_key, str):
            raise FrameError(
                "schema_violation", f"role key must be a string, got {type(raw_key)!r}"
            )
        reject_lone_surrogates(raw_key)
        key = unicodedata.normalize("NFC", raw_key)
        if key in _RESERVED_COLUMN_NAMES:
            raise FrameError(
                "schema_violation",
                f"role key {raw_key!r} collides with the reserved column name {key!r}",
            )
        if key in seen and seen[key] != raw_key:
            raise FrameError(
                "schema_violation",
                f"role key {raw_key!r} collides with {seen[key]!r} after NFC "
                "normalisation",
            )
        seen[key] = raw_key
    return tuple(sorted(seen))


def derive_columns(role_keys: Iterable[str]) -> tuple[str, ...]:
    """The pinned column order for a frame declaring `role_keys`, positional
    in every row:

    `stat_var` (the resolved StatVar's canonical dcid); one column per role
    the shape declares, named by the role property dcid verbatim and
    ordered by Unicode code point of its NFC form; `time_period`; `value`;
    `unit`; the remaining facet-identity columns (`measurementMethod`, then
    `provenance`); and `status`, the reserved qualification column, last.
    """
    roles = normalize_role_keys(role_keys)
    return (
        STAT_VAR_COLUMN,
        *roles,
        TIME_PERIOD_COLUMN,
        VALUE_COLUMN,
        UNIT_COLUMN,
        *FACET_IDENTITY_COLUMNS,
        STATUS_COLUMN,
    )


def parse_time_period(value: str) -> TimePeriod:
    """Parse a `time_period` cell into its start, end, and grain.

    A cell is one point (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`, zero-padded) or
    an ISO 8601 interval `<start>/<end>` of two points at the same grain with
    `start` strictly before `end`. A one-period window is spelled as the
    point, so each period has one spelling. `time_period` is always a
    string, never a number: a bare year like `2015` would otherwise
    round-trip through a JSON number, which has no zero-padding and no way
    to spell a month or day.

    Raises:
        FrameError: `invalid_time_period` for any other spelling.
    """
    match = _TIME_PERIOD_RE.fullmatch(value)
    if match is None:
        raise FrameError(
            "invalid_time_period",
            "time_period must be YYYY, YYYY-MM, or YYYY-MM-DD, zero-padded, "
            f"or two of them joined by '/': {value!r}",
        )
    start, end = match["start"], match["end"]
    grain = _GRAIN_BY_LENGTH[len(start)]
    if end is None:
        return TimePeriod(start=start, end=start, grain=grain)
    if len(end) != len(start):
        raise FrameError(
            "invalid_time_period",
            f"time_period interval ends must share one grain: {value!r}",
        )
    # Same-grain zero-padded ASCII points order by code point as by date.
    if start >= end:
        raise FrameError(
            "invalid_time_period",
            "time_period interval start must be before its end; "
            f"write a one-period window as the point: {value!r}",
        )
    return TimePeriod(start=start, end=end, grain=grain)


def validate_time_period(value: str) -> None:
    """Refuse a `time_period` string `parse_time_period` refuses.

    Raises:
        FrameError: `invalid_time_period`.
    """
    parse_time_period(value)


def validate_stat_var_ref(value: str) -> None:
    """Refuse a `stat_var` reference that isn't `dcid:`-prefixed.

    `value` is already known to be a non-empty, NFC-normalised string with
    no lone surrogate by the time this runs; this only narrows the generic
    ref domain to the one spelling the grammar pins, so an unprefixed dcid
    like `Count_Person` or a display label like `Population` refuses
    instead of being laundered into a distinct, permanent identity-bearing
    value alongside its `dcid:`-prefixed spelling.
    """
    if not value.startswith(STAT_VAR_REF_PREFIX):
        raise FrameError(
            "schema_violation",
            f"stat_var must be a dcid:-prefixed reference, got {value!r}",
        )


def validate_unit_request(request: Mapping[str, object]) -> None:
    """Refuse a unit-request object outside the closed shape the frame
    grammar pins for a materialised cell: `currency` (three upper-case ASCII
    letters, a concrete currency only) and `prices` (`current` or
    `constant`), with `base_year` present exactly when prices are constant,
    and an optional `recipe`, the non-empty id of the conversion recipe
    that produced the cell.

    The cell extends the unit request of `dfo_spec_kit.identity`'s
    unit-facet binding, which carries `currency`, `prices` and `base_year`
    only. The identity names the unit a spec asks for, and the cell also
    names the recipe that delivered it. A frame's own per-row `unit` cell
    is always already resolved to one concrete currency per row, so a
    role-shaped `currency` refuses.
    """
    extra = set(request) - _UNIT_CELL_KEYS
    if extra:
        raise FrameError(
            "invalid_unit", f"unit request carries unknown member(s): {sorted(extra)!r}"
        )
    if "currency" not in request or "prices" not in request:
        raise FrameError(
            "invalid_unit", "unit request requires 'currency' and 'prices'"
        )

    currency = request["currency"]
    if not is_currency_code(currency):
        raise FrameError(
            "invalid_unit", f"currency must be upper-case ISO 4217: {currency!r}"
        )

    prices = request["prices"]
    # The isinstance check runs before the membership test: an unhashable
    # value (a JSON array or object) would make `in` on a frozenset raise a
    # raw TypeError instead of this refusal.
    if not isinstance(prices, str) or prices not in _UNIT_REQUEST_PRICES:
        raise FrameError("invalid_unit", f"invalid 'prices' enum value: {prices!r}")

    has_base_year = "base_year" in request
    if prices == "constant" and not has_base_year:
        raise FrameError("invalid_unit", "prices='constant' requires 'base_year'")
    if prices == "current" and has_base_year:
        raise FrameError(
            "invalid_unit", "'base_year' is only valid when prices='constant'"
        )
    if has_base_year:
        base_year = request["base_year"]
        if isinstance(base_year, bool) or not isinstance(base_year, int):
            raise FrameError("invalid_unit", "base_year must be an integer")
        if abs(base_year) > _JCS_MAX_SAFE_INTEGER:
            raise FrameError(
                "invalid_unit",
                f"base_year must be within the JCS-serialisable integer domain "
                f"(magnitude at most 2**53 - 1), got {base_year}",
            )
    if "recipe" in request:
        recipe = request["recipe"]
        if not isinstance(recipe, str) or not recipe:
            raise FrameError("invalid_unit", "recipe must be a non-empty string")
        try:
            reject_lone_surrogates(recipe)
        except FrameError as exc:
            raise FrameError("invalid_unit", f"recipe: {exc}") from exc


@dataclass(frozen=True, slots=True)
class UnitRequest:
    """A converted frame's canonical `unit` cell value: the closed
    `{currency, prices, base_year, recipe}` shape `validate_unit_request`
    accepts, carried as a deeply immutable value rather than a plain
    mapping.

    A `Frame` is frozen only at its own top level -- `columns` and `rows`
    are tuples, but a plain `dict` nested inside one of those tuples would
    still let a caller mutate a cell after `Frame.build` has already
    checked row uniqueness and sorted rows against it, silently
    invalidating both without the `Frame` object itself ever changing
    identity. This type has no mutable state for a caller to reach:
    `as_json` is the only way back to a plain JSON object, and it always
    builds a fresh one rather than exposing anything held internally.
    """

    currency: str
    prices: str
    base_year: int | None = None
    recipe: str | None = None

    def as_json(self) -> dict[str, object]:
        """The plain JSON object form `canonicalize` and JCS-based
        sort-key comparison require, built fresh on every call. A member
        that is `None` is left out."""
        result: dict[str, object] = {"currency": self.currency, "prices": self.prices}
        if self.base_year is not None:
            result["base_year"] = self.base_year
        if self.recipe is not None:
            result["recipe"] = self.recipe
        return result


@dataclass(frozen=True, slots=True)
class StatVarPart:
    """One side of a `ComposedUnit` whose rows declare no unit: the
    `{"stat_var": ...}` object naming the StatVar that side counts, a
    `dcid:`-prefixed reference under the `stat_var` column's own rule.
    """

    stat_var: str

    def as_json(self) -> dict[str, object]:
        """The plain JSON object form, built fresh on every call."""
        return {"stat_var": self.stat_var}


# One side of a composed unit: a unit ref, a converted unit, or the StatVar
# a unitless side counts.
type UnitPart = str | UnitRequest | StatVarPart


@dataclass(frozen=True, slots=True)
class ComposedUnit:
    """A `unit` cell stating the unit of a quotient of two measures: the
    closed `{numerator, denominator}` shape `unit_cell_from_json` accepts,
    carried as a deeply immutable value as `UnitRequest` is.

    Each side is a `UnitPart`. A side is never null and never itself a
    `ComposedUnit`.
    """

    numerator: UnitPart
    denominator: UnitPart

    def as_json(self) -> dict[str, object]:
        """The plain JSON object form `canonicalize` and JCS-based
        sort-key comparison require, built fresh on every call."""
        return {
            "numerator": unit_cell_to_json(self.numerator),
            "denominator": unit_cell_to_json(self.denominator),
        }


# A non-null `unit` cell as `Frame.rows` gives it: a unit ref, a converted
# unit, or a composed unit.
type UnitCell = str | UnitRequest | ComposedUnit


def unit_cell_to_json(
    cell: UnitCell | UnitPart | None,
) -> str | dict[str, object] | None:
    """The JSON form of a `unit` cell, or of one side of a composed unit:
    `None`, the ref string itself, or the object's `as_json()`. This is the
    value canonical frame bytes carry in the `unit` column.
    """
    if cell is None or isinstance(cell, str):
        return cell
    return cell.as_json()


def unit_cell_from_json(value: object) -> UnitCell | None:
    """Validate the JSON form of a `unit` cell and return the cell.

    `None` returns `None`. A string is NFC-normalised into a unit ref. An
    object carrying `numerator` or `denominator` is read as a composed
    unit, and any other object as a unit request under
    `validate_unit_request`.

    A composed unit is exactly `numerator` and `denominator`. Each is a
    non-empty string, a unit-request object, or an object with exactly
    `stat_var`, a string under the `stat_var` column's rule.

    Raises:
        FrameError: `schema_violation` for a value that is none of the
            forms, an empty ref, or a cell object with a non-string key;
            `lone_surrogate` for any string holding one; `invalid_unit`
            for a unit request or a composed unit outside its closed
            shape, a non-string key in a part included.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = _normalize_text(value)
        if not text:
            raise FrameError("schema_violation", "unit must be a non-empty string")
        return text
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise FrameError("schema_violation", "unit request keys must be strings")
        unit = cast("Mapping[str, object]", value)
        if _COMPOSED_UNIT_KEYS & unit.keys():
            return _composed_unit_from_json(unit)
        return unit_request_from_json(unit)
    raise FrameError(
        "schema_violation",
        f"unit must be a string, mapping, or null, got {type(value)!r}",
    )


def validate_unit_cell(cell: object) -> None:
    """Refuse a hand-built `unit` cell that `unit_cell_from_json` would not
    return: anything but `None`, a `str`, a `UnitRequest` or a
    `ComposedUnit`, or one of those whose JSON form fails validation or
    reads back as a different cell, such as a non-NFC ref, a
    `ComposedUnit` whose side is not a `str`, a `UnitRequest` or a
    `StatVarPart`, or a `UnitRequest` outside its closed shape.

    Raises:
        FrameError: `invalid_unit`.
    """
    if cell is not None and not isinstance(cell, (str, UnitRequest, ComposedUnit)):
        raise FrameError(
            "invalid_unit",
            f"unit cell must be a string, UnitRequest, ComposedUnit, or None, "
            f"got {type(cell)!r}",
        )
    if isinstance(cell, ComposedUnit):
        for side in ("numerator", "denominator"):
            part = getattr(cell, side)
            if not isinstance(part, (str, UnitRequest, StatVarPart)):
                raise FrameError(
                    "invalid_unit",
                    f"composed unit {side} must be a string, UnitRequest, or "
                    f"StatVarPart, got {type(part)!r}",
                )
    try:
        canonical = unit_cell_from_json(unit_cell_to_json(cell))
    except FrameError as exc:
        raise FrameError("invalid_unit", f"invalid unit cell {cell!r}: {exc}") from exc
    if canonical != cell:
        raise FrameError(
            "invalid_unit",
            f"unit cell {cell!r} is not in the form validation gives it: {canonical!r}",
        )


def unit_request_from_json(request: Mapping[str, object]) -> UnitRequest:
    """Validate a unit-request object under `validate_unit_request` and
    return it as a `UnitRequest`.

    Raises:
        FrameError: `invalid_unit`.
    """
    validate_unit_request(request)
    recipe = request.get("recipe")
    return UnitRequest(
        currency=unicodedata.normalize("NFC", cast(str, request["currency"])),
        prices=cast(str, request["prices"]),
        base_year=cast("int | None", request.get("base_year")),
        recipe=None
        if recipe is None
        else unicodedata.normalize("NFC", cast(str, recipe)),
    )


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    reject_lone_surrogates(text)
    return text


def _composed_unit_from_json(unit: Mapping[str, object]) -> ComposedUnit:
    extra = set(unit) - _COMPOSED_UNIT_KEYS
    if extra:
        raise FrameError(
            "invalid_unit",
            f"composed unit carries unknown member(s): {sorted(extra)!r}",
        )
    if unit.keys() != _COMPOSED_UNIT_KEYS:
        raise FrameError(
            "invalid_unit", "composed unit requires 'numerator' and 'denominator'"
        )
    return ComposedUnit(
        numerator=_unit_part_from_json(unit["numerator"], side="numerator"),
        denominator=_unit_part_from_json(unit["denominator"], side="denominator"),
    )


def _unit_part_from_json(value: object, *, side: str) -> UnitPart:
    if isinstance(value, str):
        text = _normalize_text(value)
        if not text:
            raise FrameError(
                "invalid_unit", f"composed unit {side} must be a non-empty string"
            )
        return text
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise FrameError(
            "invalid_unit",
            f"composed unit {side} must be a unit ref, a unit request, or a "
            f"stat_var object, got {type(value)!r}",
        )
    part = cast("Mapping[str, object]", value)
    if _COMPOSED_UNIT_KEYS & part.keys():
        raise FrameError(
            "invalid_unit", f"composed unit {side} may not itself be a composed unit"
        )
    if STAT_VAR_COLUMN not in part:
        return unit_request_from_json(part)
    extra = set(part) - _STAT_VAR_PART_KEYS
    if extra:
        raise FrameError(
            "invalid_unit",
            f"composed unit {side} carries unknown member(s) beside 'stat_var': "
            f"{sorted(extra)!r}",
        )
    stat_var = part[STAT_VAR_COLUMN]
    if not isinstance(stat_var, str):
        raise FrameError(
            "invalid_unit",
            f"composed unit {side} stat_var must be a string, got {type(stat_var)!r}",
        )
    text = _normalize_text(stat_var)
    # The `stat_var` column's own rule, refused here under the unit's
    # category because the cell at fault is the `unit` cell.
    try:
        validate_stat_var_ref(text)
    except FrameError as exc:
        raise FrameError("invalid_unit", f"composed unit {side}: {exc}") from exc
    return StatVarPart(stat_var=text)


def cell_sort_key(cell: object) -> tuple:
    """Comparison key for one grain cell, under the platform's one
    collation.

    `None` sorts before every string or object -- a bare facet axis is
    "emptier" than any bound one. A string compares by Unicode code point
    of its NFC form (plain Python string comparison already does this, so
    long as the string was NFC-normalised beforehand -- frame construction
    guarantees that, this function does not re-normalise). A unit-request
    object, `recipe` included, or a composed unit compares by its own JCS
    serialisation: because JCS output is valid UTF-8, and UTF-8's byte
    order agrees with Unicode code point order for valid UTF-8, comparing
    the serialised bytes is equivalent to the same code-point collation
    every other cell uses.

    Cells order by type first (`None`, then string, then unit request,
    then composed unit). No pinned rule orders a bare unit ref against an
    object, so separating them by rank is a ruling that keeps the key
    total. A cell and its JSON form (`unit_cell_to_json`) share one key.
    """
    if cell is None:
        return (0,)
    if isinstance(cell, str):
        return (1, cell)
    if isinstance(cell, UnitRequest):
        return (2, canonicalize(cell.as_json()))
    if isinstance(cell, ComposedUnit):
        return (3, canonicalize(cell.as_json()))
    if isinstance(cell, Mapping):
        rank = 3 if _COMPOSED_UNIT_KEYS & cell.keys() else 2
        return (rank, canonicalize(dict(cell)))
    raise FrameError(
        "schema_violation", f"cell of unsupported type for sorting: {type(cell)!r}"
    )


def row_sort_key(columns: Sequence[str], cells: Sequence[object]) -> tuple:
    """The tuple that sorts a frame's rows and detects a duplicate grain:
    every cell in canonical column order except `value` and `status`, each
    run through `cell_sort_key`.

    Excluding `value` keeps a row's own observed number out of what
    identifies its grain -- two rows that differ only in `value` are a
    genuine duplicate, not two distinct observations. Excluding `status`
    does the same for the reserved qualification slot, which is always
    `null` today and would otherwise be redundant with this exclusion
    reserved for when it starts to vary.
    """
    return tuple(
        cell_sort_key(cell)
        for column, cell in zip(columns, cells, strict=True)
        if column not in (VALUE_COLUMN, STATUS_COLUMN)
    )
