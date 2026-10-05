"""The public surface: error attributes, frozen results, and kind spelling."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

import dfo_provenance_docs
from dfo_provenance_docs import (
    ChangeKind,
    ChangelogChange,
    ChangelogRelease,
    DocumentError,
    DocumentErrorReason,
    TransformationStep,
    decode_mcf_text,
    parse_changelog,
    parse_notes,
)


def test_error_carries_reason_line_and_detail() -> None:
    with pytest.raises(DocumentError) as excinfo:
        parse_notes("- One.\n\nAfterword.\n")
    error = excinfo.value
    assert error.reason is DocumentErrorReason.NOT_SINGLE_BULLET_LIST
    assert error.line == 3
    assert error.detail
    assert str(error).startswith("not_single_bullet_list at line 3: ")


def test_undecodable_error_has_no_line() -> None:
    with pytest.raises(DocumentError) as excinfo:
        decode_mcf_text('say "hi"')
    assert excinfo.value.line is None
    assert str(excinfo.value).startswith("undecodable: ")


def test_document_error_is_a_value_error() -> None:
    assert issubclass(DocumentError, ValueError)


def test_results_are_frozen() -> None:
    step = TransformationStep(title="Step", description="Body.")
    # The rejected assignment is the behaviour under test.
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.title = "Other"  # ty: ignore[invalid-assignment]


def test_changelog_values_are_typed() -> None:
    (release,) = parse_changelog("## 2026-09-22 Title\n\n### Fixed\n\n- One.\n")
    assert release == ChangelogRelease(
        date=date(2026, 9, 22),
        title="Title",
        changes=(ChangelogChange(kind=ChangeKind.FIXED, note="One."),),
    )


def test_change_kinds_are_the_keep_a_changelog_six() -> None:
    assert [kind.value for kind in ChangeKind] == [
        "added",
        "changed",
        "deprecated",
        "removed",
        "fixed",
        "security",
    ]


def test_public_names_resolve() -> None:
    for name in dfo_provenance_docs.__all__:
        assert hasattr(dfo_provenance_docs, name), name
