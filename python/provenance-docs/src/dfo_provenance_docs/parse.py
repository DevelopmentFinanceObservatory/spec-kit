"""Parse the four provenance documentation templates into typed values.

Every parser takes a decoded Markdown document, rejects it if it exceeds
`MAX_DOCUMENT_BYTES`, parses it as CommonMark with markdown-it-py, rejects any
link reference definition, and then walks the top-level blocks in document
order, raising on the first block that breaks a rule. Headings are found by
token.

Text values keep their Markdown. A paragraph is its source text with each
line break, and the spaces or tabs around it, replaced by one space. Every
other block is its source lines, copied verbatim. Blocks are joined by one
blank line.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from markdown_it import MarkdownIt, rules_inline
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from dfo_provenance_docs.errors import DocumentError, DocumentErrorReason

type _InlineRule = Callable[[StateInline, bool], bool]


def _record_offset(rule: _InlineRule) -> _InlineRule:
    """Record where in its inline source the token a rule pushes starts.

    Inline tokens carry no line map, and a code span's content turns its
    line breaks into spaces, so the line of an inline token cannot be
    recovered from the tokens before it. The offset is read as
    `token.meta["offset"]`, and counts from the start of the source the
    token was parsed from: the block's inline content, or the alt text of
    the image that holds it.
    """

    def recorded(state: StateInline, silent: bool) -> bool:
        start, pushed = state.pos, len(state.tokens)
        matched = rule(state, silent)
        if len(state.tokens) > pushed:
            state.tokens[-1].meta["offset"] = start
        return matched

    return recorded


_PARSER = MarkdownIt("commonmark")
# The rules that push the inline tokens a document may not hold, and the
# image rule, whose alt text holds inline tokens of its own: html_inline
# pushes raw HTML, and newline and escape push hard breaks.
for _name in ("html_inline", "newline", "escape", "image"):
    _PARSER.inline.ruler.at(_name, _record_offset(getattr(rules_inline, _name)))

# The largest document, in UTF-8 bytes, that a parser accepts.
MAX_DOCUMENT_BYTES = 64 * 1024

# How deep block quotes and list items may nest inside one another.
MAX_CONTAINER_DEPTH = 8

# markdown-it stops tokenising block content at `maxNesting` token levels
# and leaves it out of the token stream, while the verbatim copy of the
# enclosing block would still carry it. A block quote adds one token level
# and a list item two (the list, then the item), so the container that
# breaks the depth limit opens at level 2 * MAX_CONTAINER_DEPTH + 1 or
# lower. Keeping that below the cutoff means every rejected container is
# tokenised, and no accepted content sits beyond the cutoff unexamined.
# Inline nesting cannot reach the cutoff. Only a link raises the inline
# level while tokenising, links cannot contain links, and image alt text is
# parsed from level 0.
assert _PARSER.options["maxNesting"] > 2 * MAX_CONTAINER_DEPTH + 1

_CONTAINER_OPEN = frozenset({"blockquote_open", "list_item_open"})
_CONTAINER_CLOSE = frozenset({"blockquote_close", "list_item_close"})
# Unicode categories a heading may not hold: control characters (C0, C1 and
# DEL), the line separator and the paragraph separator.
_NOT_PLAIN_TEXT = frozenset({"Cc", "Zl", "Zp"})

# Body blocks allowed in transformation steps and methodology. Paragraphs
# serialise with soft wraps joined; the rest serialise as source lines.
_VERBATIM_BLOCKS = frozenset(
    {"bullet_list", "ordered_list", "fence", "code_block", "blockquote"}
)

_RELEASE_HEADING = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?: (\S.*))?")


class ChangeKind(StrEnum):
    """A Keep a Changelog group. The heading is the value, capitalised."""

    ADDED = "added"
    CHANGED = "changed"
    DEPRECATED = "deprecated"
    REMOVED = "removed"
    FIXED = "fixed"
    SECURITY = "security"


_CHANGE_KINDS = {kind.value.capitalize(): kind for kind in ChangeKind}


@dataclass(frozen=True, slots=True)
class TransformationStep:
    """One `##` section of a transformations document.

    Attributes:
        title: The heading text.
        description: The section body as CommonMark.
    """

    title: str
    description: str


@dataclass(frozen=True, slots=True)
class ChangelogChange:
    """One bullet under a changelog group.

    Attributes:
        kind: The group the bullet sits under.
        note: The bullet's paragraph as CommonMark.
    """

    kind: ChangeKind
    note: str


@dataclass(frozen=True, slots=True)
class ChangelogRelease:
    """One `##` release of a changelog document.

    Attributes:
        date: The release date.
        title: The release title after the date.
        changes: Every bullet in the release, group by group in document order.
    """

    date: date
    title: str
    changes: tuple[ChangelogChange, ...]


# A block is one top-level token, or an opening token through its closing
# token inclusive.
type _Block = tuple[Token, ...]


@dataclass(slots=True)
class _Section:
    title: str
    line: int
    body: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Release:
    date: date
    title: str
    line: int
    changes: list[ChangelogChange] = field(default_factory=list)
    kinds: set[ChangeKind] = field(default_factory=set)


@dataclass(slots=True)
class _Group:
    kind: ChangeKind
    line: int
    filled: bool = False


def parse_transformations(markdown: str) -> tuple[TransformationStep, ...]:
    """Parse a transformations document into its steps.

    The document is one or more `##` sections. The heading is the step title
    and the body is one or more paragraphs, lists, code blocks or block
    quotes.

    Args:
        markdown: The decoded document.

    Returns:
        The steps in document order.

    Raises:
        DocumentError: The document breaks the template.
    """
    lines, blocks = _parse(markdown)
    sections: list[_Section] = []
    for block in blocks:
        _check_block(block)
        head = block[0]
        if head.type == "heading_open":
            _require_level(block, 2)
            _close_section(sections)
            sections.append(_Section(title=_heading_text(block), line=_line(head)))
        elif not sections:
            raise _outside_section(block)
        else:
            sections[-1].body.append(_serialise(block, lines))
    _close_section(sections)
    return tuple(
        TransformationStep(title=s.title, description="\n\n".join(s.body))
        for s in sections
    )


def parse_changelog(markdown: str) -> tuple[ChangelogRelease, ...]:
    """Parse a changelog document into its releases.

    The document is one or more `## YYYY-MM-DD <title>` releases, newest
    first, with unique dates. Each release holds one or more `###` groups
    named after a Keep a Changelog kind, each kind at most once, and each
    group is exactly one bullet list whose items are one paragraph each.

    Args:
        markdown: The decoded document.

    Returns:
        The releases in document order.

    Raises:
        DocumentError: The document breaks the template.
    """
    _, blocks = _parse(markdown)
    releases: list[_Release] = []
    group: _Group | None = None
    for block in blocks:
        _check_block(block)
        head = block[0]
        level = _heading_level(block)
        if level == 2:
            _close_group(group)
            group = None
            _close_release(releases)
            releases.append(
                _release(block, previous=releases[-1] if releases else None)
            )
        elif level == 3:
            if not releases:
                raise _outside_section(block)
            _close_group(group)
            group = _group(block, releases[-1])
        elif level is not None:
            _require_level(block, 3 if releases else 2)
        elif group is None:
            raise _outside_section(block)
        elif group.filled or head.type != "bullet_list_open":
            raise DocumentError(
                reason=DocumentErrorReason.NOT_SINGLE_BULLET_LIST,
                line=_line(head),
                detail=f"a {group.kind.value} group holds exactly one bullet list",
            )
        else:
            releases[-1].changes.extend(
                ChangelogChange(kind=group.kind, note=note)
                for note in _list_paragraphs(block)
            )
            group.filled = True
    _close_group(group)
    _close_release(releases)
    return tuple(
        ChangelogRelease(date=r.date, title=r.title, changes=tuple(r.changes))
        for r in releases
    )


def parse_prose(markdown: str) -> str:
    """Parse a methodology document: one or more blocks and no headings.

    Args:
        markdown: The decoded document.

    Returns:
        The document as CommonMark.

    Raises:
        DocumentError: The document breaks the template.
    """
    lines, blocks = _parse(markdown)
    parts: list[str] = []
    for block in blocks:
        _check_block(block)
        if block[0].type == "heading_open":
            raise DocumentError(
                reason=DocumentErrorReason.UNEXPECTED_HEADING_LEVEL,
                line=_line(block[0]),
                detail="this document holds no headings",
            )
        parts.append(_serialise(block, lines))
    return "\n\n".join(parts)


def parse_notes(markdown: str) -> tuple[str, ...]:
    """Parse a notes document: exactly one bullet list of one-paragraph items.

    Args:
        markdown: The decoded document.

    Returns:
        Each item's paragraph as CommonMark, in document order.

    Raises:
        DocumentError: The document breaks the template.
    """
    _, blocks = _parse(markdown)
    for index, block in enumerate(blocks):
        _check_block(block)
        if index > 0 or block[0].type != "bullet_list_open":
            raise DocumentError(
                reason=DocumentErrorReason.NOT_SINGLE_BULLET_LIST,
                line=_line(block[0]),
                detail="the document is exactly one bullet list",
            )
    return _list_paragraphs(blocks[0])


def _parse(markdown: str) -> tuple[list[str], list[_Block]]:
    # The ceiling bounds markdown-it's worst cases, some of which grow with
    # the square of the input. Every character is at least one byte, so a
    # long string is rejected before it is encoded.
    if (
        len(markdown) > MAX_DOCUMENT_BYTES
        or len(markdown.encode("utf-8", "surrogatepass")) > MAX_DOCUMENT_BYTES
    ):
        raise DocumentError(
            reason=DocumentErrorReason.DOCUMENT_TOO_LARGE,
            line=None,
            detail=f"the document exceeds {MAX_DOCUMENT_BYTES} bytes of UTF-8",
        )
    # markdown-it normalises line endings and NUL before parsing, and its
    # token maps index the normalised lines, so the verbatim copy of a block
    # reads the same normalised text.
    text = re.sub(r"\r\n?", "\n", markdown).replace("\0", "�")
    env: dict[str, Any] = {}
    blocks = _group_blocks(_PARSER.parse(text, env), level=0)
    # A link reference definition emits no token, so a block copied or
    # joined from source would keep a reference link and lose its target.
    # markdown-it records each definition in env with its 0-based line map.
    if references := env.get("references"):
        line = min(reference["map"][0] for reference in references.values()) + 1
        raise DocumentError(
            reason=DocumentErrorReason.DISALLOWED_BLOCK,
            line=line,
            detail="link reference definition; write links inline",
        )
    if not blocks:
        raise DocumentError(
            reason=DocumentErrorReason.EMPTY_DOCUMENT,
            line=None,
            detail="the document holds no blocks",
        )
    return text.split("\n"), blocks


def _group_blocks(tokens: list[Token], *, level: int) -> list[_Block]:
    blocks: list[_Block] = []
    start = 0
    for index, token in enumerate(tokens):
        if token.level != level:
            continue
        if token.nesting == 1:
            start = index
        elif token.nesting == -1:
            blocks.append(tuple(tokens[start : index + 1]))
        else:
            blocks.append((token,))
    return blocks


def _line(token: Token) -> int:
    assert token.map is not None, f"{token.type} token carries no line map"
    return token.map[0] + 1


def _check_block(block: _Block) -> None:
    """Reject deep nesting, raw HTML, hard breaks, thematic breaks and nested
    headings."""
    depth = 0
    for token in block:
        if token.type in _CONTAINER_CLOSE:
            depth -= 1
        elif token.type in _CONTAINER_OPEN:
            depth += 1
            if depth > MAX_CONTAINER_DEPTH:
                raise DocumentError(
                    reason=DocumentErrorReason.NESTING_TOO_DEEP,
                    line=_line(token),
                    detail=(
                        "block quotes and list items nest at most "
                        f"{MAX_CONTAINER_DEPTH} deep"
                    ),
                )
        elif token.type == "html_block":
            raise DocumentError(
                reason=DocumentErrorReason.RAW_HTML,
                line=_line(token),
                detail="raw HTML block",
            )
        if token.type == "hr":
            raise DocumentError(
                reason=DocumentErrorReason.DISALLOWED_BLOCK,
                line=_line(token),
                detail="thematic break",
            )
        if token.type == "heading_open" and token.level > 0:
            raise DocumentError(
                reason=DocumentErrorReason.DISALLOWED_BLOCK,
                line=_line(token),
                detail="heading inside a list or block quote",
            )
        if token.type == "inline":
            _check_inline(token, line=_line(token))


def _check_inline(parent: Token, *, line: int) -> None:
    """Reject raw HTML and hard breaks among `parent`'s inline children.

    `parent` is a block's inline token or an image, and `line` is the line
    its source starts on. Its content holds that source with a line break
    per source line, including block quote and list item continuations, so
    a child's line is `line` plus the breaks before the child's offset. An
    image's alt text starts on the image's own line and is checked the same
    way, at any depth.
    """
    for child in parent.children or ():
        if child.type not in {"html_inline", "hardbreak", "image"}:
            continue
        child_line = line + parent.content.count("\n", 0, child.meta["offset"])
        if child.type == "html_inline":
            raise DocumentError(
                reason=DocumentErrorReason.RAW_HTML,
                line=child_line,
                detail=f"inline HTML {child.content!r}",
            )
        if child.type == "hardbreak":
            raise DocumentError(
                reason=DocumentErrorReason.HARD_BREAK,
                line=child_line,
                detail="hard line break at the end of this line",
            )
        _check_inline(child, line=child_line)


def _heading_level(block: _Block) -> int | None:
    head = block[0]
    return int(head.tag[1:]) if head.type == "heading_open" else None


def _require_level(block: _Block, expected: int) -> None:
    level = _heading_level(block)
    if level != expected:
        raise DocumentError(
            reason=DocumentErrorReason.UNEXPECTED_HEADING_LEVEL,
            line=_line(block[0]),
            detail=f"expected a level {expected} heading, found level {level}",
        )


def _heading_text(block: _Block) -> str:
    children = block[1].children or []
    if not children:
        raise DocumentError(
            reason=DocumentErrorReason.EMPTY_HEADING,
            line=_line(block[0]),
            detail="the heading has no text",
        )
    if any(child.type != "text" for child in children):
        kinds = sorted({child.type for child in children} - {"text"})
        raise DocumentError(
            reason=DocumentErrorReason.HEADING_NOT_PLAIN_TEXT,
            line=_line(block[0]),
            detail=f"the heading holds {', '.join(kinds)}",
        )
    text = "".join(child.content for child in children)
    if controls := sorted(
        {c for c in text if unicodedata.category(c) in _NOT_PLAIN_TEXT}
    ):
        raise DocumentError(
            reason=DocumentErrorReason.HEADING_NOT_PLAIN_TEXT,
            line=_line(block[0]),
            detail=(
                "the heading holds control or line separator characters "
                f"{', '.join(f'U+{ord(c):04X}' for c in controls)}"
            ),
        )
    return text


def _outside_section(block: _Block) -> DocumentError:
    return DocumentError(
        reason=DocumentErrorReason.CONTENT_OUTSIDE_SECTION,
        line=_line(block[0]),
        detail="content before the first heading that owns it",
    )


def _empty_section(line: int) -> DocumentError:
    return DocumentError(
        reason=DocumentErrorReason.EMPTY_SECTION,
        line=line,
        detail="the heading has no content",
    )


def _close_section(sections: list[_Section]) -> None:
    if sections and not sections[-1].body:
        raise _empty_section(sections[-1].line)


def _close_release(releases: list[_Release]) -> None:
    if releases and not releases[-1].kinds:
        raise _empty_section(releases[-1].line)


def _close_group(group: _Group | None) -> None:
    if group is not None and not group.filled:
        raise _empty_section(group.line)


def _release(block: _Block, *, previous: _Release | None) -> _Release:
    text = _heading_text(block)
    line = _line(block[0])
    match = _RELEASE_HEADING.fullmatch(text)
    try:
        released = date.fromisoformat(match.group(1)) if match else None
    except ValueError:
        released = None
    if match is None or released is None:
        raise DocumentError(
            reason=DocumentErrorReason.INVALID_RELEASE_HEADING,
            line=line,
            detail=f"{text!r} is not 'YYYY-MM-DD <title>' with a calendar date",
        )
    title = match.group(2)
    if title is None:
        raise DocumentError(
            reason=DocumentErrorReason.MISSING_RELEASE_TITLE,
            line=line,
            detail=f"{text!r} has no title after the date",
        )
    if previous is not None and released == previous.date:
        raise DocumentError(
            reason=DocumentErrorReason.DUPLICATE_RELEASE_DATE,
            line=line,
            detail=f"{released} repeats the release above",
        )
    if previous is not None and released > previous.date:
        raise DocumentError(
            reason=DocumentErrorReason.RELEASE_DATES_NOT_DESCENDING,
            line=line,
            detail=f"{released} is later than the release above, {previous.date}",
        )
    return _Release(date=released, title=title, line=line)


def _group(block: _Block, release: _Release) -> _Group:
    text = _heading_text(block)
    line = _line(block[0])
    kind = _CHANGE_KINDS.get(text)
    if kind is None:
        raise DocumentError(
            reason=DocumentErrorReason.UNKNOWN_CHANGE_KIND,
            line=line,
            detail=f"{text!r} is not one of {', '.join(_CHANGE_KINDS)}",
        )
    if kind in release.kinds:
        raise DocumentError(
            reason=DocumentErrorReason.DUPLICATE_CHANGE_KIND,
            line=line,
            detail=f"{text} appears twice in the {release.date} release",
        )
    release.kinds.add(kind)
    return _Group(kind=kind, line=line)


def _list_paragraphs(block: _Block) -> tuple[str, ...]:
    """Return each item of a bullet list as one serialised paragraph."""
    notes: list[str] = []
    for item in _group_blocks(list(block[1:-1]), level=block[0].level + 1):
        inner = [token.type for token in item[1:-1]]
        if inner != ["paragraph_open", "inline", "paragraph_close"]:
            raise DocumentError(
                reason=DocumentErrorReason.LIST_ITEM_NOT_SINGLE_PARAGRAPH,
                line=_line(item[0]),
                detail="a bullet holds exactly one paragraph",
            )
        notes.append(_join_soft_wraps(item[2].content))
    return tuple(notes)


def _serialise(block: _Block, lines: list[str]) -> str:
    head = block[0]
    if head.type == "paragraph_open":
        return _join_soft_wraps(block[1].content)
    kind = head.type.removesuffix("_open")
    assert kind in _VERBATIM_BLOCKS, f"{kind} passed _check_block unrejected"
    assert head.map is not None, f"{head.type} token carries no line map"
    source = lines[head.map[0] : head.map[1]]
    while source and not source[-1].strip():
        source.pop()
    return "\n".join(source)


def _join_soft_wraps(content: str) -> str:
    """Replace each line break, and the spaces or tabs around it, with one space.

    A scan over the lines, so a long run of spaces costs linear time.
    """
    first, *rest = content.split("\n")
    if not rest:
        return content
    *middle, last = rest
    return " ".join(
        [
            first.rstrip(" \t"),
            *(part.strip(" \t") for part in middle),
            last.lstrip(" \t"),
        ]
    )
