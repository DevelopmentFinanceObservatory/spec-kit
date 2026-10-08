"""Runs `dfo_frame_grammar.rolling` against its recorded vectors, shipped as
package data at `src/dfo_frame_grammar/vectors/rolling/rolling-vectors.json`
(see the README beside it for the record format), and checks what a JSON
vector cannot spell: series keys that are not strings, and the package-root
exports.
"""

from __future__ import annotations

import importlib.resources
import json
from fractions import Fraction

import pytest

import dfo_frame_grammar
from dfo_frame_grammar.grammar import FrameError
from dfo_frame_grammar.rolling import (
    ANCHOR_RULE,
    ANCHOR_SHARE,
    Anchor,
    RollingError,
    latest_anchor,
    shift_period,
)

_VECTORS = json.loads(
    (
        importlib.resources.files("dfo_frame_grammar")
        / "vectors"
        / "rolling"
        / "rolling-vectors.json"
    ).read_text(encoding="utf-8")
)
_ANCHOR_VECTORS = _VECTORS["latest_anchor"]
_SHIFT_VECTORS = _VECTORS["shift_period"]

_MIN_ANCHOR_VECTORS = 19
_MIN_SHIFT_VECTORS = 16


def _refusal(excinfo: pytest.ExceptionInfo[ValueError]) -> str:
    error = excinfo.value
    if isinstance(error, RollingError):
        return error.code
    assert isinstance(error, FrameError), error
    return error.category


def test_suite_meets_its_floor_and_names_each_vector_once() -> None:
    """A truncated vector file fails the floor."""
    assert len(_ANCHOR_VECTORS) >= _MIN_ANCHOR_VECTORS
    assert len(_SHIFT_VECTORS) >= _MIN_SHIFT_VECTORS
    names = [vector["name"] for vector in (*_ANCHOR_VECTORS, *_SHIFT_VECTORS)]
    assert len(names) == len(set(names))
    assert all(("expected" in v) != ("error" in v) for v in _ANCHOR_VECTORS)
    assert all(("expected" in v) != ("error" in v) for v in _SHIFT_VECTORS)


def test_vectors_were_recorded_under_the_current_anchor_rule() -> None:
    """The half rule and its name move together, and the recorded vectors
    state the rule they hold for."""
    assert _VECTORS["anchor_rule"] == ANCHOR_RULE == "half1"
    assert Fraction(1, 2) == ANCHOR_SHARE


@pytest.mark.parametrize("vector", _ANCHOR_VECTORS, ids=lambda v: v["name"])
def test_latest_anchor_vector(vector: dict) -> None:
    if "error" in vector:
        with pytest.raises(ValueError) as excinfo:
            latest_anchor(vector["populations"])
        assert _refusal(excinfo) == vector["error"]
        return

    anchor = latest_anchor(vector["populations"])
    assert anchor.period == vector["expected"]["period"]
    assert [list(entry) for entry in anchor.left_out] == vector["expected"]["left_out"]


@pytest.mark.parametrize("vector", _SHIFT_VECTORS, ids=lambda v: v["name"])
def test_shift_period_vector(vector: dict) -> None:
    if "error" in vector:
        with pytest.raises(ValueError) as excinfo:
            shift_period(vector["period"], vector["offset"])
        assert _refusal(excinfo) == vector["error"]
        return

    assert shift_period(vector["period"], vector["offset"]) == vector["expected"]


@pytest.mark.parametrize("offset", [-1.0, "-1", True, None])
def test_shift_period_takes_an_int_offset_only(offset: object) -> None:
    """A JSON `integer` may arrive as `-1.0`, and the caller converts it."""
    with pytest.raises(TypeError):
        shift_period("2024", offset)


def test_series_keys_may_be_any_hashable_and_come_back_unchanged() -> None:
    """A frame's series key is a tuple of coordinate values."""
    eth = ("country/ETH", "dcid:Amount")
    ken = ("country/KEN", "dcid:Amount")
    anchor = latest_anchor(
        [{eth: frozenset({"2023", "2024"}), ken: ("2023",)}, {7: ["2024"]}]
    )
    assert anchor == Anchor(period="2024", left_out=((0, ken),))


def test_package_root_exports_the_rolling_api() -> None:
    assert dfo_frame_grammar.latest_anchor is latest_anchor
    assert dfo_frame_grammar.shift_period is shift_period
    assert dfo_frame_grammar.Anchor is Anchor
    assert dfo_frame_grammar.ANCHOR_RULE == ANCHOR_RULE
    assert dfo_frame_grammar.ANCHOR_SHARE == ANCHOR_SHARE
    assert {
        dfo_frame_grammar.RollingAnchorUnresolved.code,
        dfo_frame_grammar.RollingGrainUnsupported.code,
    } == {"rolling_anchor_unresolved", "rolling_grain_unsupported"}
