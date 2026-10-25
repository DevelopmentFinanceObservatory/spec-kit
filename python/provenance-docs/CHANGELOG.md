# Changelog

All notable changes to the `dfo-spec-kit-provenance-docs` Python package will be
documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this package adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Package scaffolding (uv, ruff, ty, pytest).
- `decode_mcf_text`, which decodes a Markdown document from the escaped form
  the graph stores.
- `parse_transformations`, `parse_changelog`, `parse_prose` and `parse_notes`,
  which parse the four provenance documentation templates into frozen
  dataclasses and raise `DocumentError` with a `DocumentErrorReason` and the
  line of the construct that breaks the template.
- The vector suite as package data, with its record schema.
- A 65,536-byte ceiling on every parsed document (`MAX_DOCUMENT_BYTES`,
  reason `document_too_large`), and a limit of eight nested block quotes and
  list items (`MAX_CONTAINER_DEPTH`, reason `nesting_too_deep`), which keeps
  every accepted block inside markdown-it's tokenised depth.
- Raw HTML and hard breaks are rejected inside image alt text, and headings
  reject control, line separator and paragraph separator characters, including
  ones written as character references.

### Security

- The size ceiling bounds markdown-it-py 4.2.0's quadratic time on long runs
  of `&` (executablebooks/markdown-it-py#423, merged but in no release yet).
  Raise the `markdown-it-py` floor in `pyproject.toml` to the first release
  that contains that fix.

[unreleased]: https://github.com/DevelopmentFinanceObservatory/spec-kit/commits/main/python
