"""Grammar conformance: every intent this bundle ships must validate
against `schemas/intent.v0.json`, the write-side intent grammar.

Proves the shipped intents (`intents/*.minimal.json`,
`fixtures/*/intent.json`) are grammar-conformant, and pins the grammar's
`$id` (the release token callers must match) and its `oneOf` shape.
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from dfo_spec_kit_components import bundle_dir

_BUNDLE = bundle_dir()
_GRAMMAR = json.loads(
    (_BUNDLE / "schemas" / "intent.v0.json").read_text(encoding="utf-8")
)
_VALIDATOR = Draft202012Validator(_GRAMMAR)

_SHIPPED_INTENT_PATHS = [
    "intents/line_chart.minimal.json",
    "intents/bar_chart.minimal.json",
    "intents/table.minimal.json",
    "intents/stat_card.minimal.json",
    "intents/note_narration.minimal.json",
    "intents/source_card.minimal.json",
    "fixtures/line_chart/intent.json",
    "fixtures/bar_chart/intent.json",
    "fixtures/table/intent.json",
    "fixtures/stat_card/intent.json",
    "fixtures/note_narration/intent.json",
    "fixtures/source_card/intent.json",
]


def test_grammar_id_names_the_release() -> None:
    assert _GRAMMAR["$id"] == "dfo/presentation/intent.v0"


def test_grammar_is_a_oneof_over_six_component_shapes() -> None:
    assert len(_GRAMMAR["oneOf"]) == 6


@pytest.mark.parametrize("relative_path", _SHIPPED_INTENT_PATHS)
def test_shipped_intent_validates_against_the_grammar(relative_path: str) -> None:
    intent = json.loads((_BUNDLE / relative_path).read_text(encoding="utf-8"))
    _VALIDATOR.validate(intent)


@pytest.mark.parametrize(
    "relative_path",
    [
        "intents/line_chart.minimal.json",
        "intents/bar_chart.minimal.json",
        "intents/table.minimal.json",
        "intents/stat_card.minimal.json",
    ],
)
def test_minimal_intents_select_exactly_one_dimension(relative_path: str) -> None:
    """ "Minimal" means the smallest intent that still validates: one
    dimension, one value, nothing else."""
    intent = json.loads((_BUNDLE / relative_path).read_text(encoding="utf-8"))
    assert set(intent) == {"type", "frame_ref", "select"}
    assert len(intent["select"]) == 1


@pytest.mark.parametrize(
    ("relative_path", "expected_type"),
    [
        ("fixtures/line_chart/intent.json", "line_chart"),
        ("fixtures/stat_card/intent.json", "stat_card"),
    ],
)
def test_worked_fixture_intents_select_multiple_dimensions_with_scalar_values(
    relative_path: str, expected_type: str
) -> None:
    """The worked fixtures carry a realistic multi-dimension selection:
    every dimension here has exactly one bound value, spelled as a scalar
    string."""
    intent = json.loads((_BUNDLE / relative_path).read_text(encoding="utf-8"))
    assert intent["type"] == expected_type
    assert len(intent["select"]) >= 2
    assert all(isinstance(value, str) for value in intent["select"].values())


@pytest.mark.parametrize(
    ("relative_path", "expected_type"),
    [
        ("fixtures/bar_chart/intent.json", "bar_chart"),
        ("fixtures/table/intent.json", "table"),
    ],
)
def test_worked_fixture_intents_carry_an_arrayed_dimension(
    relative_path: str, expected_type: str
) -> None:
    """The bar_chart and table worked fixtures carry a realistic
    multi-dimension selection with one dimension bound to an array of
    values, the shape their own worked examples exist to show off."""
    intent = json.loads((_BUNDLE / relative_path).read_text(encoding="utf-8"))
    assert intent["type"] == expected_type
    assert len(intent["select"]) >= 2
    assert any(isinstance(value, list) for value in intent["select"].values())


def test_note_narration_minimal_intent_is_type_and_text_only() -> None:
    """note_narration's grammar shape is `{type, text}`, no `frame_ref` and
    no `select` -- its minimal intent is its whole writable shape, not a
    one-dimension slice of a larger one."""
    intent = json.loads(
        (_BUNDLE / "intents/note_narration.minimal.json").read_text(encoding="utf-8")
    )
    assert set(intent) == {"type", "text"}
    assert intent["type"] == "note_narration"
    assert isinstance(intent["text"], str) and intent["text"]


def test_source_card_minimal_intent_is_type_and_frame_ref_only() -> None:
    """source_card's grammar shape is `{type, frame_ref}`, no `select` --
    its minimal intent is its whole writable shape."""
    intent = json.loads(
        (_BUNDLE / "intents/source_card.minimal.json").read_text(encoding="utf-8")
    )
    assert set(intent) == {"type", "frame_ref"}
    assert intent["type"] == "source_card"


@pytest.mark.parametrize("side", ["start", "end"])
@pytest.mark.parametrize(
    ("bound", "admitted"),
    [
        ("2024-01-01/2024-01-31", True),
        ("2024-01-01/2024-01-310", False),
    ],
    ids=["day_span_21_chars", "22_chars"],
)
def test_window_bound_admits_a_day_grain_span(
    side: str, bound: str, admitted: bool
) -> None:
    """A window bound holds the longest period the frame grammar parses, a
    day-grain span of 21 characters, and refuses anything longer."""
    intent = json.loads(
        (_BUNDLE / "intents/table.minimal.json").read_text(encoding="utf-8")
    )
    intent["window"] = {side: bound}
    assert _VALIDATOR.is_valid(intent) is admitted
