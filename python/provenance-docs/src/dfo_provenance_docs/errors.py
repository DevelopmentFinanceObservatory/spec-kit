"""The error every decoder and parser in this package raises, and its reasons."""

from __future__ import annotations

from enum import StrEnum


class DocumentErrorReason(StrEnum):
    """Why a stored value or document breaks the provenance documentation grammar.

    The values are permanent once published. A changed meaning gets a new
    reason. The vector record schema beside the vector suite lists the same
    values, and the package tests hold the two sets equal.
    """

    UNDECODABLE = "undecodable"
    """The stored value is not the inside of a JSON string, or decodes to a
    lone surrogate that has no UTF-8 encoding."""

    DOCUMENT_TOO_LARGE = "document_too_large"
    """The decoded document is longer than 65,536 bytes of UTF-8
    (`MAX_DOCUMENT_BYTES`)."""

    EMPTY_DOCUMENT = "empty_document"
    """The document holds nothing but whitespace."""

    CONTENT_OUTSIDE_SECTION = "content_outside_section"
    """A block sits before the first heading that would own it: before the
    first `##` of a sectioned document, or between a release heading and its
    first `###` group."""

    UNEXPECTED_HEADING_LEVEL = "unexpected_heading_level"
    """A heading at a level the template does not use at that point."""

    HEADING_NOT_PLAIN_TEXT = "heading_not_plain_text"
    """A heading holds code, emphasis, a link, an image or a line break, or its
    text, after character references are decoded, holds a control character
    (C0, C1 or DEL), a line separator or a paragraph separator."""

    EMPTY_HEADING = "empty_heading"
    """A heading has no text."""

    EMPTY_SECTION = "empty_section"
    """A heading has no content before the next heading or the end."""

    DISALLOWED_BLOCK = "disallowed_block"
    """A block type the template does not allow: a thematic break, a heading
    inside a list or block quote, or a link reference definition."""

    NESTING_TOO_DEEP = "nesting_too_deep"
    """Block quotes and list items nest more than eight deep
    (`MAX_CONTAINER_DEPTH`)."""

    RAW_HTML = "raw_html"
    """Raw HTML, as a block or inline."""

    HARD_BREAK = "hard_break"
    """A hard line break: two trailing spaces or a trailing backslash."""

    INVALID_RELEASE_HEADING = "invalid_release_heading"
    """A changelog release heading is not a calendar date in `YYYY-MM-DD`
    form followed by one space and a title."""

    MISSING_RELEASE_TITLE = "missing_release_title"
    """A changelog release heading is a valid date with no title."""

    RELEASE_DATES_NOT_DESCENDING = "release_dates_not_descending"
    """A changelog release is dated later than the release above it."""

    DUPLICATE_RELEASE_DATE = "duplicate_release_date"
    """A changelog release has the same date as the release above it."""

    UNKNOWN_CHANGE_KIND = "unknown_change_kind"
    """A changelog group heading is not one of `Added`, `Changed`,
    `Deprecated`, `Removed`, `Fixed` or `Security`."""

    DUPLICATE_CHANGE_KIND = "duplicate_change_kind"
    """A changelog group heading appears twice in one release."""

    NOT_SINGLE_BULLET_LIST = "not_single_bullet_list"
    """A changelog group, or a notes document, is anything other than exactly
    one bullet list."""

    LIST_ITEM_NOT_SINGLE_PARAGRAPH = "list_item_not_single_paragraph"
    """A changelog or notes bullet holds anything other than one paragraph."""


class DocumentError(ValueError):
    """A stored value or document breaks the provenance documentation grammar.

    Attributes:
        reason: The rule the document breaks.
        line: The 1-based line in the decoded document of the construct
            that breaks the rule. For inline HTML or a hard break, the line
            that holds the HTML or ends in the break. Otherwise the first
            line of the offending block. `None` when the error has no line
            (an undecodable value, an oversized document, an empty
            document).
        detail: A human-readable explanation for logs.
    """

    def __init__(
        self, *, reason: DocumentErrorReason, line: int | None, detail: str
    ) -> None:
        where = "" if line is None else f" at line {line}"
        super().__init__(f"{reason}{where}: {detail}")
        self.reason = reason
        self.line = line
        self.detail = detail
