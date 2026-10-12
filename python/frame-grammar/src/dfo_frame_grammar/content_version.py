"""`content_version`: preimage construction, minting, recompute, and
`FrameTrace`, the lineage fragment a caller's trace carries.

Normative source: `docs/frame-grammar-v1.md`, which pins the preimage's
closed member set and the public token shape. The canonicalisation
discipline -- JCS and a domain separator -- is `dfo_spec_kit.identity.core`'s,
reused here through `dfo_spec_kit.identity.canonicalize` rather than
re-implemented.

A `content_version` names one logical frame: the hash of the frame's own
canonical bytes (nested as a digest, never inlined -- see
`canonical_frame_bytes` in `dfo_frame_grammar.frame`; embedding the frame
itself would make golden vectors unpublishable and cost O(frame) twice for
nothing) under this family's domain separator. Identical frame content is
one version, whatever lineage produced it.

What moves a `content_version`, and what does not
-------------------------------------------------
Moves it: the frame's own bytes, through `frame_sha256`.

Does not move it: everything else. The graph and source revisions read, the
facet selections and their bases, a conversion recipe and its table
versions, `spec_id`, `applied_window`, coverage, `materialiser_build`,
default-policy versions, `selection_reason`, `dispatch_facet_id`, and the
published `FRAME_GRAMMAR_VERSION` are lineage or request bookkeeping that a
caller records beside the token. None of them is a parameter of
`mint_content_version` or `recompute_content_version`. A byte-rule change
already moves the token through `frame_sha256`, so a document-only revision
or an additive minor grammar bump leaves every token standing still.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar.frame import (
    Frame,
    stream_canonical_frame_bytes,
)

__all__ = [
    "ContentVersionError",
    "FrameTrace",
    "frame_sha256",
    "mint_content_version",
    "recompute_content_version",
    "stream_frame_sha256",
]

# The domain separator hashed into a content_version preimage, following
# the "spec1" precedent: a short, permanent literal naming this hash family.
# It differs from `cv1`, the retired family whose preimage also hashed
# lineage, so a token from this family never equals a `cv1` token minted
# over a different preimage.
IDENTITY_VERSION = "cv2"

_FRAME_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContentVersionError(ValueError):
    """A refusal during preimage construction.

    ``category`` names the kind of refusal: ``invalid_digest`` for a
    `frame_sha256` that is not exactly 64 lowercase hex characters. Kept
    stable so a caller can match on failure kind, the same way
    `FrameError.category` and `dfo_spec_kit.identity.IdentityError.category`
    do.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _build_preimage(*, frame_sha256: str) -> dict[str, object]:
    """The closed content_version preimage document: the domain separator
    and the frame digest, nothing else."""
    if not isinstance(frame_sha256, str) or not _FRAME_SHA256_RE.fullmatch(
        frame_sha256
    ):
        raise ContentVersionError(
            "invalid_digest",
            f"frame_sha256 must be 64 lowercase hex characters, got {frame_sha256!r}",
        )
    return {"identity_version": IDENTITY_VERSION, "frame_sha256": frame_sha256}


def frame_sha256(frame: Frame) -> str:
    """The full 64 lowercase hex SHA-256 digest of `frame`'s own canonical
    (JCS) bytes -- exactly the value `mint_content_version`'s own
    `frame_sha256` keyword argument requires.

    A caller who instead hand-rolls
    `hashlib.sha256(canonical_frame_bytes(frame)).hexdigest()` has one more
    place to get wrong -- a truncated digest, or one taken over some other
    byte sequence -- and a wrong `frame_sha256` mints a permanently wrong,
    but otherwise well-formed, `content_version`. This helper exists so
    there is exactly one way to produce the value, not because the
    computation itself is complex.

    Hashes the pieces of `stream_canonical_frame_bytes` as they are
    produced, so neither the whole document nor more than one slice of rows
    is held in memory. Raises `dfo_frame_grammar.grammar.FrameError`
    `row_key_not_increasing` for a table whose rows are not in canonical
    order, which only `Frame.from_table` or the `Frame` constructor can
    wrap.
    """
    return stream_frame_sha256([frame])


def stream_frame_sha256(chunks: Iterable[Frame]) -> str:
    """The full 64 lowercase hex SHA-256 digest of the concatenation of
    `dfo_frame_grammar.frame.stream_canonical_frame_bytes(chunks)` --
    exactly the value `frame_sha256` computes for a frame already held
    whole in memory, computed here from validated `Frame` chunks without
    ever holding more than one chunk's rows, or the concatenated bytes, in
    memory at once. Raises `dfo_frame_grammar.grammar.FrameError` on the
    same refusals `stream_canonical_frame_bytes` raises: a chunk whose
    `columns` differs from the first chunk's, or a row whose
    `row_sort_key` is not strictly greater than the previous row's.
    """
    hasher = hashlib.sha256()
    for piece in stream_canonical_frame_bytes(chunks):
        hasher.update(piece)
    return hasher.hexdigest()


def mint_content_version(*, frame_sha256: str) -> str:
    """Mint a `content_version`: `cv_` plus the first 32 lowercase hex
    characters of SHA-256 over the canonical (JCS) preimage
    `{"identity_version": "cv2", "frame_sha256": frame_sha256}`.

    `frame_sha256` is the full 64 lowercase hex digest of
    `dfo_frame_grammar.frame.canonical_frame_bytes(frame)` -- a nested
    digest, not the frame inline (see the module-level `frame_sha256` and
    `stream_frame_sha256` functions above, which compute exactly this
    value). It is the only parameter: lineage and request fields have no
    way in.

    Raises:
        ContentVersionError: `invalid_digest` for a `frame_sha256` that is
            not exactly 64 lowercase hex characters.
    """
    canonical = canonicalize(_build_preimage(frame_sha256=frame_sha256))
    digest = hashlib.sha256(canonical).hexdigest()
    return f"cv_{digest[:32]}"


@dataclass(frozen=True)
class FrameTrace:
    """The lineage of one materialised frame, for a caller's own trace:
    the graph and source revisions read, the facet selections, the
    conversion record or the trace's one-entry conversion array (`None`
    when no recipe ran), and `frame_grammar_version`.

    A plain dataclass, not a validating one. Nothing in this module reads
    it: `content_version` is the frame digest alone, so no field here
    reaches the preimage.
    """

    graph_revision: str
    source_revisions: tuple[Mapping[str, object], ...]
    facets: tuple[Mapping[str, object], ...]
    frame_grammar_version: str
    conversion: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None


def recompute_content_version(canonical_bytes: bytes) -> str:
    """Recompute the `content_version` a stored frame's canonical bytes
    mint, as an integrity check.

    Hashes `canonical_bytes` exactly as given -- it is never re-parsed and
    the frame is never re-serialised from it -- so a mutated stored byte
    changes `frame_sha256` and is detected as a mismatch rather than
    silently laundered back to the original bytes by a round trip through
    the frame grammar. Callers therefore must persist the canonical frame
    bytes themselves, or a lossless form this library re-canonicalises back
    to the same bytes; anything else makes this function unable to do its
    job.
    """
    return mint_content_version(
        frame_sha256=hashlib.sha256(canonical_bytes).hexdigest()
    )
