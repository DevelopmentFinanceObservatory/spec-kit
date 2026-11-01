"""The structure rules a step list and a description must meet beyond their
JSON Schemas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from itertools import pairwise
from typing import Any

import rfc8785

MAX_STEPS = 64
"""The most steps one list may hold."""

MAX_STEPS_BYTES = 65_536
"""The most bytes a stored step list may take as JCS (RFC 8785) UTF-8 JSON."""

MAX_CITE_TEXT_LENGTH = 2048
"""The most characters a description's `cite.text` may hold, the bound the
description schema sets on `$defs.cite.properties.text`."""

_SIDES_OPS = frozenset({"compare"})
"""The operations whose step must record `sides`. The schema defines the member
for any two-sided operation, and only these require it."""


class StepsErrorReason(StrEnum):
    """Why a schema-valid step list or description breaks the structure rules.

    The values are permanent once published. A changed meaning gets a new
    reason. The vector record schema lists the same values, and the package
    tests hold the two sets equal.
    """

    TOO_MANY_STEPS = "too_many_steps"
    """The list holds more than `MAX_STEPS` steps."""

    STEPS_TOO_LARGE = "steps_too_large"
    """The stored list takes more than `MAX_STEPS_BYTES` as JCS UTF-8 JSON."""

    NOT_I_JSON = "not_i_json"
    """The list holds a value I-JSON (RFC 7493) cannot carry, so JCS cannot
    serialise it: a lone surrogate, a non-finite number or an integer
    outside ±(2**53 - 1)."""

    DUPLICATE_STEP_ID = "duplicate_step_id"
    """A step's id repeats an earlier step's."""

    UNKNOWN_INPUT = "unknown_input"
    """An input names a step id no step in the list holds."""

    INPUT_NOT_EARLIER = "input_not_earlier"
    """An input names the step itself or a later step. Inputs that name only
    earlier steps cannot form a cycle, so this is the cycle rule too."""

    MISSING_SIDES = "missing_sides"
    """A compare step has no `sides`, so a reader cannot name the variable it
    writes from the entities each side holds."""

    SIDES_ROLES_MISMATCH = "sides_roles_mismatch"
    """A compare step's `sides` keys are not exactly the step's input roles."""

    UNKNOWN_CAVEAT_STEP = "unknown_caveat_step"
    """A description's caveat names a step its `steps` do not hold."""

    UNKNOWN_GAP_STEP = "unknown_gap_step"
    """A description's gap names a step its `steps` do not hold."""

    DRAWN_VALUES_COUNT_MISMATCH = "drawn_values_count_mismatch"
    """A drawn dimension lists `values` whose number differs from its
    `count`."""

    DUPLICATE_DRAWN_VALUE = "duplicate_drawn_value"
    """A drawn dimension's `values` repeat a `ref`."""

    SOURCE_CODES_NOT_ASCENDING = "source_codes_not_ascending"
    """A `source_codes` list, under `about` or on a drawn value, is not
    strictly ascending by provenance and then code: it is out of order or
    lists a `(provenance, code)` pair twice, with or without `exact`."""


class StepsError(ValueError):
    """A step list or description breaks the structure rules.

    Attributes:
        reason: The rule the list or description breaks.
        step: The 0-based position of the step that breaks it, or `None` for
            a rule on the whole list or on a caveat or gap.
        detail: A human-readable explanation for logs.
    """

    def __init__(
        self, *, reason: StepsErrorReason, step: int | None, detail: str
    ) -> None:
        where = "" if step is None else f" at step {step}"
        super().__init__(f"{reason}{where}: {detail}")
        self.reason = reason
        self.step = step
        self.detail = detail


def steps_bytes(steps: Sequence[Mapping[str, Any]]) -> int:
    """The bytes a stored step list takes as JCS (RFC 8785) UTF-8 JSON.

    JCS spells numbers as ECMAScript does, so a TypeScript consumer counts
    the same bytes with `JSON.stringify` on the same list.

    Args:
        steps: A step list that already validates against
            `steps.schema.json`.

    Raises:
        StepsError: With `NOT_I_JSON` when JCS cannot serialise a value.
    """
    try:
        return len(rfc8785.dumps([dict(step) for step in steps]))
    except rfc8785.CanonicalizationError as error:
        raise StepsError(
            reason=StepsErrorReason.NOT_I_JSON, step=None, detail=str(error)
        ) from error


def check_steps(steps: Sequence[Mapping[str, Any]]) -> None:
    """Check a stored step list against the structure rules.

    The bound is on the stored list, so this is where it is checked. The
    bounds come first, so an oversized list costs one serialisation. Then
    each step in order: its id must be new, every input that names a step
    must name an earlier one, and a compare step must carry `sides` keyed by
    exactly its input roles. An input that is a version reference always
    passes.

    Args:
        steps: A step list that already validates against
            `steps.schema.json`. Anything else may raise `KeyError` or
            `TypeError`.

    Raises:
        StepsError: On the first rule the list breaks.
    """
    _check_count(steps)
    size = steps_bytes(steps)
    if size > MAX_STEPS_BYTES:
        raise StepsError(
            reason=StepsErrorReason.STEPS_TOO_LARGE,
            step=None,
            detail=f"{size} bytes, at most {MAX_STEPS_BYTES}",
        )
    _check_each_step(steps)


def check_description(description: Mapping[str, Any]) -> None:
    """Check a description against the structure rules.

    Its `steps`, when they are a list, must meet the count bound, carry only
    values I-JSON can hold and meet the id and input rules of `check_steps`.
    The byte bound is not checked: served labels come from the vocabulary,
    so served bytes differ from the stored list's, and the bound is checked
    where the list is written. Then every
    caveat's `step`, and every gap that names a step by id, must name a
    step in `steps`. A gap names a step when its path has at least three
    segments and starts `steps.`, as in `steps.s3.op`.
    A drawn dimension that lists `values` must list `count` of them, each
    `ref` once. Every `source_codes` list, under `about` and on each drawn
    value, must be strictly ascending by provenance and then code, which
    sorts it and holds each pair once.

    Args:
        description: A description that already validates against
            `description.schema.json`. Anything else may raise `KeyError`
            or `TypeError`.

    Raises:
        StepsError: On the first rule the description breaks.
    """
    steps = description["steps"]
    if steps is not None:
        _check_count(steps)
        steps_bytes(steps)
        _check_each_step(steps)
    held = {step["id"] for step in steps or []}
    for position, caveat in enumerate(description["caveats"]):
        if "step" in caveat and caveat["step"] not in held:
            raise StepsError(
                reason=StepsErrorReason.UNKNOWN_CAVEAT_STEP,
                step=None,
                detail=f"caveat {position} names step {caveat['step']!r}",
            )
    for gap in description["gaps"]:
        segments = gap.split(".")
        if segments[0] == "steps" and len(segments) > 2 and segments[1] not in held:
            raise StepsError(
                reason=StepsErrorReason.UNKNOWN_GAP_STEP,
                step=None,
                detail=f"gap {gap!r} names step {segments[1]!r}",
            )
    _check_drawn_values(description)
    _check_source_codes(description)


def _check_count(steps: Sequence[Mapping[str, Any]]) -> None:
    if len(steps) > MAX_STEPS:
        raise StepsError(
            reason=StepsErrorReason.TOO_MANY_STEPS,
            step=None,
            detail=f"{len(steps)} steps, at most {MAX_STEPS}",
        )


def _check_each_step(steps: Sequence[Mapping[str, Any]]) -> None:
    every_id = {step["id"] for step in steps}
    seen: set[str] = set()
    for position, step in enumerate(steps):
        step_id = step["id"]
        if step_id in seen:
            raise StepsError(
                reason=StepsErrorReason.DUPLICATE_STEP_ID,
                step=position,
                detail=f"step id {step_id!r} is already taken",
            )
        for role, named in _step_inputs(step):
            if named in seen:
                continue
            reason = (
                StepsErrorReason.INPUT_NOT_EARLIER
                if named in every_id
                else StepsErrorReason.UNKNOWN_INPUT
            )
            raise StepsError(
                reason=reason,
                step=position,
                detail=f"input {role!r} names step {named!r}",
            )
        if step["op"] in _SIDES_OPS:
            _check_sides(step, position)
        seen.add(step_id)


def _check_sides(step: Mapping[str, Any], position: int) -> None:
    if "sides" not in step:
        raise StepsError(
            reason=StepsErrorReason.MISSING_SIDES,
            step=position,
            detail=f"{step['op']} step {step['id']!r} has no sides",
        )
    roles = set(step.get("inputs", {}))
    if set(step["sides"]) != roles:
        raise StepsError(
            reason=StepsErrorReason.SIDES_ROLES_MISMATCH,
            step=position,
            detail=f"sides {sorted(step['sides'])} are not the input roles {sorted(roles)}",
        )


def _step_inputs(step: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The step ids a step's inputs name, each with its role, in order.

    A single `input` reads as role `input`. Version references are skipped.
    """
    inputs = {"input": step["input"]} if "input" in step else step.get("inputs", {})
    return [(role, named) for role, named in inputs.items() if isinstance(named, str)]


def _check_drawn_values(description: Mapping[str, Any]) -> None:
    """A dimension's `values`, when present, are `count` distinct refs."""
    drawn = description["about"].get("drawn") or {}
    for position, dimension in enumerate(drawn.get("dimensions", [])):
        values = dimension.get("values")
        if values is None:
            continue
        if len(values) != dimension["count"]:
            raise StepsError(
                reason=StepsErrorReason.DRAWN_VALUES_COUNT_MISMATCH,
                step=None,
                detail=(
                    f"drawn dimension {position} lists {len(values)} values "
                    f"for a count of {dimension['count']}"
                ),
            )
        refs = [value["ref"] for value in values]
        if len(set(refs)) != len(refs):
            raise StepsError(
                reason=StepsErrorReason.DUPLICATE_DRAWN_VALUE,
                step=None,
                detail=f"drawn dimension {position} lists a ref twice",
            )


def _check_source_codes(description: Mapping[str, Any]) -> None:
    """Each `source_codes` list is strictly ascending by `(provenance, code)`."""
    about = description["about"]
    lists = [("about.source_codes", about.get("source_codes"))]
    drawn = about.get("drawn") or {}
    for position, dimension in enumerate(drawn.get("dimensions", [])):
        for index, value in enumerate(dimension.get("values") or []):
            where = f"drawn dimension {position} value {index}"
            lists.append((where, value.get("source_codes")))
    for where, codes in lists:
        keys = [(code["provenance"], code["code"]) for code in codes or []]
        for earlier, later in pairwise(keys):
            if earlier >= later:
                raise StepsError(
                    reason=StepsErrorReason.SOURCE_CODES_NOT_ASCENDING,
                    step=None,
                    detail=f"{where} lists {later} after {earlier}",
                )
