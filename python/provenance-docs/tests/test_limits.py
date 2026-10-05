"""The document size ceiling, the nesting limit, and linear-time soft wraps.

The soft-wrap bounds sit well above a linear join of a document at the ceiling
and well below a regex that backtracks over a run of spaces, which takes about
2.4 s at the ceiling. markdown-it-py 4.2.0 is quadratic on a run of `&`, and
its cost at the ceiling depends on the machine, so the ceiling test checks that
the limit is applied before parsing and the `&` test checks only the result.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from dfo_provenance_docs import (
    MAX_CONTAINER_DEPTH,
    MAX_DOCUMENT_BYTES,
    DocumentError,
    DocumentErrorReason,
    parse_notes,
    parse_prose,
)
from dfo_provenance_docs.parse import _PARSER


def _timed[T](parse: Callable[[str], T], markdown: str) -> tuple[T, float]:
    start = time.perf_counter()
    result = parse(markdown)
    return result, time.perf_counter() - start


def test_a_run_of_spaces_at_the_ceiling_parses_in_linear_time() -> None:
    markdown = "x" + " " * (MAX_DOCUMENT_BYTES - 2) + "y"
    result, elapsed = _timed(parse_prose, markdown)
    assert result == markdown
    assert elapsed < 0.5


def test_a_wrapped_note_with_a_run_of_spaces_joins_in_linear_time() -> None:
    run = " " * (MAX_DOCUMENT_BYTES - 16)
    result, elapsed = _timed(parse_notes, f"- x{run}y\n  \tz\n")
    assert result == (f"x{run}y z",)
    assert elapsed < 0.5


def test_a_run_of_ampersands_at_the_ceiling_parses() -> None:
    markdown = "&" * MAX_DOCUMENT_BYTES
    assert parse_prose(markdown) == markdown


@pytest.mark.parametrize(
    ("character", "count"),
    [("a", MAX_DOCUMENT_BYTES), ("é", MAX_DOCUMENT_BYTES // 2)],
    ids=["one-byte", "two-byte"],
)
def test_the_ceiling_counts_utf8_bytes(character: str, count: int) -> None:
    assert parse_prose(character * count) == character * count
    with pytest.raises(DocumentError) as excinfo:
        parse_prose(character * (count + 1))
    assert excinfo.value.reason is DocumentErrorReason.DOCUMENT_TOO_LARGE
    assert excinfo.value.line is None


def test_the_ceiling_is_checked_before_parsing() -> None:
    """A document far past the ceiling is refused without being parsed."""
    start = time.perf_counter()
    with pytest.raises(DocumentError) as excinfo:
        parse_prose("&" * (16 * MAX_DOCUMENT_BYTES))
    assert time.perf_counter() - start < 0.1
    assert excinfo.value.reason is DocumentErrorReason.DOCUMENT_TOO_LARGE


def test_the_nesting_limit_stays_below_the_parser_cutoff() -> None:
    """The container that breaks the limit opens below markdown-it's cutoff.

    A list item adds two token levels, so the opener of container
    `MAX_CONTAINER_DEPTH + 1` sits at level `2 * MAX_CONTAINER_DEPTH + 1` at
    most, and markdown-it tokenises every block below `maxNesting`.
    """
    assert _PARSER.options["maxNesting"] > 2 * MAX_CONTAINER_DEPTH + 1
    levels = [
        token.level
        for token in _PARSER.parse(
            "".join(
                f"{'  ' * depth}- {depth}\n" for depth in range(MAX_CONTAINER_DEPTH + 1)
            )
        )
        if token.type == "list_item_open"
    ]
    assert levels[-1] == 2 * MAX_CONTAINER_DEPTH + 1


def test_lists_nest_to_the_limit() -> None:
    markdown = "".join(
        f"{'  ' * depth}- {depth}\n" for depth in range(MAX_CONTAINER_DEPTH)
    )
    assert parse_prose(markdown) == markdown.rstrip("\n")
