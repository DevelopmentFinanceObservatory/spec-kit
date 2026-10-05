# dfo-spec-kit-provenance-docs (Python)

This package decodes the Markdown documents a Provenance node carries in the graph and parses each against its template. The templates and rules below are the normative contract. The packaged vector suite pins them, and where this README and the vectors disagree, the vectors win and the disagreement is a bug. See the [repo README](../../README.md) for how this package fits with the rest of `dfo-spec-kit`.

The import name is `dfo_provenance_docs`. It depends on `markdown-it-py` only.

## Installation

This package uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync
```

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run ty check src/dfo_provenance_docs
```

## Usage

```python
from dfo_provenance_docs import DocumentError, decode_mcf_text, parse_transformations

try:
    steps = parse_transformations(decode_mcf_text(stored_value))
except DocumentError as error:
    print(error.reason, error.line, error.detail)
```

| Function                          | Returns                                                                                           |
| --------------------------------- | ------------------------------------------------------------------------------------------------- |
| `decode_mcf_text(value)`          | The Markdown document a stored value encodes                                                      |
| `parse_transformations(markdown)` | `tuple[TransformationStep(title, description), ...]`                                              |
| `parse_changelog(markdown)`       | `tuple[ChangelogRelease(date, title, changes), ...]`, each change a `ChangelogChange(kind, note)` |
| `parse_prose(markdown)`           | The methodology document as one string                                                            |
| `parse_notes(markdown)`           | `tuple[str, ...]`, one string per note                                                            |

Every function is pure. The results are frozen dataclasses. `MAX_DOCUMENT_BYTES` (65,536) and `MAX_CONTAINER_DEPTH` (8) hold the two limits in the rules below. `ChangelogRelease.date` is a `datetime.date` and `ChangelogChange.kind` is a `ChangeKind`, whose values are the six group names in lower case (`added`, `changed`, `deprecated`, `removed`, `fixed`, `security`).

Every function raises `DocumentError` when its input breaks the grammar. The error carries `reason`, a `DocumentErrorReason`; `line`, the 1-based line in the decoded document of the construct that breaks the rule, or `None` for an undecodable value, an oversized document or an empty document; and `detail`, a sentence for logs. For inline HTML and hard breaks the line is the one that holds the HTML or ends in the break, including a continuation line of a paragraph, list item or block quote, and a line inside an image's alt text. For a document nested too deep it is the first line of the block quote or list item that breaks the limit. For every other rule it is the first line of the offending block, heading or list item. A document either parses whole or raises, so no function returns part of a document.

## Encoding

The import pipeline writes each document as a JSON string literal with every `\"` spelled `\u0022`, because MCF ends a value at any quote. The graph stores the characters between the outer quotes. `decode_mcf_text` reads that value as the body of a JSON string. A raw quote, a raw control character, an escape JSON does not define, or an escape that decodes to a lone surrogate is `undecodable`.

The encoding applies to four properties, `dataTransformationDescription`, `dataTransformationChangelog`, `sourceMethodologyDescription` and `dataNotes`, and to AgentSkill `text`. Plain text properties such as `description`, `dataGranularity` and `licenseAttribution` are stored as written and are not decoded.

## Templates

### `transformations.md` → `dataTransformationDescription`

One `##` section per step, in pipeline order. The heading is the step title, and the paragraphs are its description.

```markdown
## Download the source data

What this step does and why, in one or more paragraphs. `Code spans`,
*emphasis* and [links](https://example.org) are fine.

## Exclude donor core contributions to multilaterals

The next step.
```

A step body holds one or more paragraphs, bullet or ordered lists, fenced or indented code blocks, and block quotes.

### `changelog.md` → `dataTransformationChangelog`

One `##` section per release, newest first. The heading is the date in `YYYY-MM-DD` form, one space, and a short title. Under it are one or more Keep a Changelog groups (`### Added`, `### Changed`, `### Deprecated`, `### Removed`, `### Fixed`, `### Security`), each holding one bullet list. Each bullet is one change, labelled with its group.

```markdown
## 2026-09-22 Exclude donor core contributions

### Changed

- Donor core contributions to multilateral organisations (`bi_multi = 2`) are
  excluded from bilateral flows, since they would otherwise double count.

## 2026-08-20 First release

### Added

- Dataset added to the graph.
```

### `methodology.md` → `sourceMethodologyDescription`

How the publisher collects and compiles the data, as one or more paragraphs, lists, code blocks or block quotes, with no headings.

```markdown
The OECD collects activity-level reports from each DAC member, ...

A second paragraph if needed.
```

### `notes.md` → `dataNotes`

One bullet list, one bullet per caveat, each bullet one paragraph.

```markdown
- Figures for the latest year are preliminary and revised the following spring.
- Private flows are reported by a subset of providers only.
```

## Rules

Each document is plain CommonMark, parsed with markdown-it-py's `commonmark` preset.

- A document is at most 65,536 bytes of UTF-8 and holds at least one block.
- Block quotes and list items nest at most eight deep, counting each block quote and each list item that holds the block.
- Raw HTML is rejected, as a block or inline, including inside an image's alt text.
- Hard line breaks are rejected: two trailing spaces or a trailing backslash, including inside an image's alt text.
- Thematic breaks are rejected at any depth, and so are headings inside a list or block quote.
- Links are written inline. A link reference definition is rejected.
- Headings are plain text: no code, emphasis, links, images or line breaks. Once character references are decoded, a heading holds no control character (C0, C1 or DEL), line separator (U+2028) or paragraph separator (U+2029), so `## A&#13;B` and a tab in a heading are rejected. A setext heading on one line counts as a heading of its level.
- Nothing goes before the first `##` in `transformations.md` or `changelog.md`, and every `##` has content under it.
- In `transformations.md`, every heading is `##`. `methodology.md` has no headings.
- In `changelog.md`, every release heading is a calendar date written with ASCII digits as `YYYY-MM-DD`, one space, and a title. Dates run strictly newest first, so no two releases share a date.
- Under a release, only `###` group headings and their lists appear, and nothing goes between the release heading and its first group. Group headings are spelled exactly `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed` or `Security`, and each appears at most once per release, in any order.
- A changelog group and a notes document are exactly one bullet list, and each bullet is exactly one paragraph.

### Serialisation

Text values keep their Markdown, so a character reference in a body, a note or a change stays as its source text. A paragraph, including a changelog or notes bullet, is its source text with each line break and the spaces or tabs around it replaced by one space, so a re-wrap in the source leaves the value unchanged. Every other block is its source lines, copied verbatim with trailing blank lines dropped. Blocks are joined by one blank line. CRLF and CR line endings read as LF.

### Error reasons

A parser checks the size ceiling first and link reference definitions second, then walks the blocks in document order and raises on the first block that breaks a rule.

| Reason                           | Meaning                                                                                                                     |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `undecodable`                    | The stored value is not a JSON string body, or decodes to a lone surrogate                                                  |
| `document_too_large`             | The document is longer than 65,536 bytes of UTF-8                                                                           |
| `empty_document`                 | The document holds no blocks                                                                                                |
| `content_outside_section`        | A block sits before the first heading that owns it: before the first `##`, or between a release heading and its first group |
| `unexpected_heading_level`       | A heading at a level the template does not use at that point                                                                |
| `heading_not_plain_text`         | A heading holds code, emphasis, a link, an image, a line break, or a control or separator character                         |
| `empty_heading`                  | A heading has no text                                                                                                       |
| `empty_section`                  | A heading has no content before the next heading or the end                                                                 |
| `disallowed_block`               | A thematic break, a heading inside a list or block quote, or a link reference definition                                    |
| `nesting_too_deep`               | Block quotes and list items nest more than eight deep                                                                       |
| `raw_html`                       | Raw HTML, as a block or inline                                                                                              |
| `hard_break`                     | A hard line break                                                                                                           |
| `invalid_release_heading`        | A release heading is not a calendar date in `YYYY-MM-DD` form, one space, and a title                                       |
| `missing_release_title`          | A release heading is a valid date with no title                                                                             |
| `release_dates_not_descending`   | A release is dated later than the release above it                                                                          |
| `duplicate_release_date`         | A release has the same date as the release above it                                                                         |
| `unknown_change_kind`            | A group heading is not one of the six kinds                                                                                 |
| `duplicate_change_kind`          | A group heading appears twice in one release                                                                                |
| `not_single_bullet_list`         | A changelog group or a notes document is anything other than exactly one bullet list                                        |
| `list_item_not_single_paragraph` | A changelog or notes bullet holds anything other than one paragraph                                                         |

Reasons are permanent once published. A changed meaning gets a new reason.

## Vector suite

`src/dfo_provenance_docs/vectors/` ships with the package as the grammar's language-neutral conformance suite. Its README defines the record format.

```bash
# Validate the vectors against their schema, without importing this package
uv run ../../tools/validate_provenance_docs_vectors.py

# Drive the implementation against every vector
uv run pytest tests/test_vectors.py
```
