"""The public surface of `check_steps`: error attributes and the bound order."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from dfo_descriptions import (
    MAX_CITE_TEXT_LENGTH,
    MAX_STEPS,
    MAX_STEPS_BYTES,
    StepsError,
    StepsErrorReason,
    check_steps,
    description_schema,
)

_VERSION = {
    "id": "spec_4b1f0c2d9e8a7b6c5d4e3f2a1b0c9d8e",
    "content_version": "cv_0f1e2d3c4b5a69788796a5b4c3d2e1f0",
    "label": "Gross ODA disbursements",
}


def test_error_carries_reason_step_and_detail() -> None:
    steps = [{"id": "s1", "op": "growth", "input": "s2"}]
    with pytest.raises(StepsError) as excinfo:
        check_steps(steps)
    error = excinfo.value
    assert error.reason is StepsErrorReason.UNKNOWN_INPUT
    assert error.step == 0
    assert error.detail
    assert str(error).startswith("unknown_input at step 0: ")


def test_a_bound_error_has_no_step() -> None:
    steps = [{"id": f"s{i}", "op": "retrieve", "input": _VERSION} for i in range(65)]
    with pytest.raises(StepsError) as excinfo:
        check_steps(steps)
    assert excinfo.value.step is None
    assert str(excinfo.value).startswith("too_many_steps: ")


def test_the_count_bound_is_checked_before_the_size_bound() -> None:
    label = "x" * 2000
    steps = [
        {"id": f"s{i}", "op": "retrieve", "input": {**_VERSION, "label": label}}
        for i in range(MAX_STEPS + 1)
    ]
    with pytest.raises(StepsError) as excinfo:
        check_steps(steps)
    assert excinfo.value.reason is StepsErrorReason.TOO_MANY_STEPS


def test_the_size_bound_counts_utf8_bytes() -> None:
    # Each "é" is one character and two bytes, so a list under the bound in
    # characters breaks it in bytes.
    label = "é" * (MAX_STEPS_BYTES // 2)
    steps = [{"id": "s1", "op": "retrieve", "input": {**_VERSION, "label": label}}]
    with pytest.raises(StepsError) as excinfo:
        check_steps(steps)
    assert excinfo.value.reason is StepsErrorReason.STEPS_TOO_LARGE


def test_a_step_may_be_any_mapping() -> None:
    check_steps([MappingProxyType({"id": "s1", "op": "retrieve", "input": _VERSION})])


def test_an_input_naming_a_version_passes_whatever_its_id() -> None:
    check_steps(
        [
            {
                "id": "s1",
                "op": "compare",
                "inputs": {"left": _VERSION},
                "sides": {"left": {}},
            }
        ]
    )


def test_a_compare_step_without_sides_fails_the_check() -> None:
    steps = [
        {"id": "s1", "op": "retrieve", "input": _VERSION},
        {"id": "s2", "op": "compare", "inputs": {"left": "s1", "right": _VERSION}},
    ]
    with pytest.raises(StepsError) as excinfo:
        check_steps(steps)
    assert excinfo.value.reason is StepsErrorReason.MISSING_SIDES
    assert excinfo.value.step == 1


@pytest.mark.parametrize("sides", [{"left": {}}, {"left": {}, "right": {}, "x": {}}])
def test_a_compare_step_whose_sides_miss_an_input_role_fails_the_check(
    sides: dict[str, dict[str, str]],
) -> None:
    steps = [
        {"id": "s1", "op": "retrieve", "input": _VERSION},
        {
            "id": "s2",
            "op": "compare",
            "inputs": {"left": "s1", "right": _VERSION},
            "sides": sides,
        },
    ]
    with pytest.raises(StepsError) as excinfo:
        check_steps(steps)
    assert excinfo.value.reason is StepsErrorReason.SIDES_ROLES_MISMATCH
    assert excinfo.value.step == 1


def test_the_cite_text_bound_is_the_schemas() -> None:
    cite_text = description_schema()["$defs"]["cite"]["properties"]["text"]
    assert cite_text["maxLength"] == MAX_CITE_TEXT_LENGTH
