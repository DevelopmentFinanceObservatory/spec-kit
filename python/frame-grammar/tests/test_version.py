"""`FRAME_GRAMMAR_VERSION` -- the published grammar document's own semver,
and its independence from `grammar.FRAME_VERSION`, the byte-rule domain
separator.
"""

from __future__ import annotations

import re

from dfo_frame_grammar import grammar
from dfo_frame_grammar.version import FRAME_GRAMMAR_VERSION

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def test_frame_grammar_version_is_a_three_component_semver_string() -> None:
    match = _SEMVER_RE.fullmatch(FRAME_GRAMMAR_VERSION)
    assert match is not None, (
        f"not a MAJOR.MINOR.PATCH semver string: {FRAME_GRAMMAR_VERSION!r}"
    )


def test_major_component_matches_the_frame1_generation() -> None:
    # grammar.FRAME_VERSION == "frame1" implies major version 1 -- the rule
    # this document's own "Document versioning" section pins. A future
    # frame2 generation would imply major 2, and this test's own literal
    # would need updating alongside that generation change, not silently
    # drift out of sync with it.
    assert grammar.FRAME_VERSION == "frame1"
    major, _minor, _patch = FRAME_GRAMMAR_VERSION.split(".")
    assert major == "1"


def test_frame_grammar_version_is_distinct_from_the_byte_rule_domain_separator() -> (
    None
):
    # The two tokens must never be spelled identically by coincidence --
    # they name different things (a document revision vs. a byte-rule
    # generation) and must stay independently readable as such.
    assert FRAME_GRAMMAR_VERSION != grammar.FRAME_VERSION
