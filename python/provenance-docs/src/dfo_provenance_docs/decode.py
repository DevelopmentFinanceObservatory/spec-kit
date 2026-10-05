"""Decode a Markdown document from the escaped form the graph stores."""

from __future__ import annotations

import json

from dfo_provenance_docs.errors import DocumentError, DocumentErrorReason


def decode_mcf_text(value: str) -> str:
    """Return the Markdown document a graph property value encodes.

    The import pipeline writes each document as a JSON string literal with
    every `\\"` spelled `\\u0022`, since MCF ends a value at any quote. The
    graph stores the characters between the outer quotes, so the value is the
    body of a JSON string and decodes as one.

    Only the Markdown document properties and AgentSkill `text` carry this
    encoding. Plain text properties are stored as written and must not pass
    through this function.

    Args:
        value: The property value as the graph returns it.

    Returns:
        The decoded Markdown document.

    Raises:
        DocumentError: `undecodable` when the value is not a JSON string body
            (a raw quote, a raw control character, a bad escape) or decodes to
            a lone surrogate.
    """
    try:
        text: str = json.loads(f'"{value}"')
    except json.JSONDecodeError as exc:
        raise DocumentError(
            reason=DocumentErrorReason.UNDECODABLE,
            line=None,
            detail=f"not a JSON string body: {exc.msg} at character {exc.pos - 1}",
        ) from exc
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DocumentError(
            reason=DocumentErrorReason.UNDECODABLE,
            line=None,
            detail=f"decodes to a lone surrogate at character {exc.start}",
        ) from exc
    return text
