"""The provenance documentation grammar.

Decodes the MCF-escaped Markdown documents a Provenance node carries and
parses each against its template: transformations, changelog, methodology
and notes. Every function is pure. Each raises `DocumentError`, whose
`reason` is drawn from `DocumentErrorReason`, when its input breaks the
grammar.
"""

from dfo_provenance_docs.decode import decode_mcf_text
from dfo_provenance_docs.errors import DocumentError, DocumentErrorReason
from dfo_provenance_docs.parse import (
    MAX_CONTAINER_DEPTH,
    MAX_DOCUMENT_BYTES,
    ChangeKind,
    ChangelogChange,
    ChangelogRelease,
    TransformationStep,
    parse_changelog,
    parse_notes,
    parse_prose,
    parse_transformations,
)

__all__ = [
    "MAX_CONTAINER_DEPTH",
    "MAX_DOCUMENT_BYTES",
    "ChangeKind",
    "ChangelogChange",
    "ChangelogRelease",
    "DocumentError",
    "DocumentErrorReason",
    "TransformationStep",
    "decode_mcf_text",
    "parse_changelog",
    "parse_notes",
    "parse_prose",
    "parse_transformations",
]
