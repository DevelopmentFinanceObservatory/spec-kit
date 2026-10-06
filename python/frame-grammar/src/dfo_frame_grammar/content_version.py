"""`content_version`: preimage construction, minting, recompute, and
`FrameTrace`, the lineage fragment this library owns.

Normative source: `docs/frame-grammar-v1.md`, which pins the preimage's
closed member set, its array orderings, and both public token shapes. The
canonicalisation discipline -- JCS, a domain separator, NFC, the one
code-point collation -- is `dfo_spec_kit.identity.core`'s, reused here
through `dfo_spec_kit.identity.canonicalize` rather than re-implemented.

A `content_version` names one logical materialised result plus the lineage
that produced it: the frame's own canonical bytes (nested as a digest,
never inlined -- see `canonical_frame_bytes` in `dfo_frame_grammar.frame`;
embedding the frame itself would make golden vectors unpublishable and cost
O(frame) twice for nothing), the graph revision and per-source revisions
read, the per-axis facet selections with their bases, and any conversion
recipe with the rate and deflator table versions it joined. Two
definitions that serve identical lineage over an identical frame mint the
same token, which is what lets `(spec_id, content_version)` name a logical
result rather than a particular byte sequence.

The preimage is built from a closed allowlist of named arguments -- never
by serialising a lineage or trace object wholesale. Every member this
module hashes is named explicitly in `_build_preimage` below; nothing
reachable from some other structure can enter just because it happens to
be in scope at the call site.

What moves a `content_version`, and what deliberately does not
----------------------------------------------------------------
Moves it: the frame's own bytes, through `frame_sha256`; `graph_revision`;
any `source_revisions` entry, or one being added or removed; any `facets`
entry -- its `ref` (or, for a converted unit axis, its unit-request
object), or its `basis` flipping between `bound` and `declared_default`; a
`conversion` record being added, removed, or having any of its members
change.

Deliberately does not move it: `spec_id` -- the preimage's closed member
set omits it, and distinct definitions serving the same frame under the
same lineage must mint the *same* token, since `(spec_id, content_version)`
names a logical result, not a byte sequence; folding `spec_id` in would
make that pairing meaningless. The published `FRAME_GRAMMAR_VERSION` -- a
byte-rule change already moves the token through `frame_sha256`, so a
document-only revision or an additive minor bump (the reserved `status`
column populating on zero rows, say) must leave the token standing still.
`frame_grammar_version` rides `FrameTrace` for the trace's own benefit and
is never read by `_build_preimage`. `applied_window`, coverage,
`materialiser_build` (a deploy must not rotate every token), default-policy
versions, `selection_reason`, and `dispatch_facet_id` -- none of these
describe the result or the lineage that produced it, so none of them are
parameters this module's minting functions accept; they belong to the
wider manifest trace instead.

JCS does not sort arrays, so this module states its own ordering rule for
the two array members: `source_revisions` sorts by `source`, and `facets`
sorts by `axis`, both by Unicode code point of the NFC form -- the
platform's one collation, the same one `dfo_frame_grammar.grammar` uses for
frame bytes. Two entries sharing a `source` (or `axis`) are refused rather
than tie-broken: one generation reads one revision per source and makes
one facet selection per axis, so a shared key is contradictory input, not
two values this module could order either way and still call
order-independent.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar.frame import (
    Frame,
    stream_canonical_frame_bytes,
)
from dfo_frame_grammar.grammar import FrameError

__all__ = [
    "ContentVersionError",
    "FrameTrace",
    "frame_sha256",
    "mint_content_version",
    "recompute_content_version",
    "stream_frame_sha256",
]

# The domain separator hashed into a frame-level content_version preimage,
# following the "spec1" precedent: a short, permanent literal naming
# this hash family so it can never collide with a differently-shaped
# preimage that happened to hash the same bytes.
IDENTITY_VERSION = "cv1"

_FRAME_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_SOURCE_REVISION_KEYS = frozenset({"source", "revision"})

# The closed facet axis vocabulary this platform declares (see
# `dfo_spec_kit.identity.core`'s own `_FACET_AXES`): `unit` is included here
# because a preimage's `facets` member records the axis actually selected,
# unlike `dfo_frame_grammar.grammar.FACET_IDENTITY_COLUMNS`, which excludes
# `unit` because the frame's own `unit` column already carries that axis's
# per-row identity.
_FACET_AXES = frozenset({"measurementMethod", "provenance", "unit"})
_FACET_KEYS = frozenset({"axis", "ref", "basis"})
_FACET_BASES = frozenset({"bound", "declared_default"})

_CONVERSION_KEYS = frozenset({"recipe", "rate_table_version", "deflator_table_version"})
_CONVERSION_REQUIRED_KEYS = frozenset({"recipe", "rate_table_version"})
# The descriptive members a manifest trace's `conversion` entry carries beside
# the three preimage members: the converted StatVars with their native units,
# and the fallback and projected counts. None of them is hashed.
_CONVERSION_TRACE_ONLY_KEYS = frozenset({"members", "fallback", "projected"})


class ContentVersionError(ValueError):
    """A refusal at some stage of preimage construction or minting.

    ``category`` names the kind of refusal: ``schema_violation`` for a
    malformed input this module has no more specific name for,
    ``unknown_member`` for a closed object carrying a member outside its
    allowed set, ``invalid_digest`` for a `frame_sha256` that is not
    exactly 64 lowercase hex characters, ``invalid_facet_axis`` or
    ``invalid_facet_basis`` for a facet entry outside its closed
    vocabulary, ``invalid_facet_ref`` for a unit-request `ref` object on
    any axis other than `unit`, ``duplicate_source`` or ``duplicate_axis``
    for two `source_revisions` (or `facets`) entries sharing the same
    `source` (or `axis`) -- a set with no such duplicate is genuinely
    order-independent only because a duplicate is refused rather than
    tie-broken by a stable sort -- and ``conversion_null`` for an explicit
    ``null`` conversion slot (which must be omitted instead -- see
    `_build_preimage`). ``lone_surrogate`` and ``invalid_unit`` are
    reused verbatim from `dfo_frame_grammar.grammar.FrameError`, whose
    surrogate-rejection and unit-request validation this module calls
    rather than re-implementing. Kept stable so a caller can match on
    failure kind, the same way `FrameError.category` and
    `dfo_spec_kit.identity.IdentityError.category` do.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class _NotGiven:
    """Sentinel distinguishing an omitted `conversion` argument -- the
    slot's only spelling of "no recipe ran", matching how an unbound facet
    contributes no bytes at all rather than a null placeholder -- from an
    explicit `None`, which refuses (see `_build_preimage`)."""

    def __repr__(self) -> str:
        return "<conversion not given>"


_NOT_GIVEN = _NotGiven()
ConversionInput = (
    Mapping[str, object] | Sequence[Mapping[str, object]] | None | _NotGiven
)


def _reject_unknown_members(
    obj: Mapping[str, object], allowed: frozenset[str], description: str
) -> None:
    extra = set(obj) - allowed
    if extra:
        raise ContentVersionError(
            "unknown_member",
            f"{description} carries unknown member(s): {sorted(extra)!r}",
        )


def _nfc_string(value: object, *, description: str) -> str:
    """NFC-normalise a required string member, reusing
    `grammar.reject_lone_surrogates` rather than re-implementing the
    surrogate check -- re-raised as `ContentVersionError` so every refusal
    this module's public functions can raise shares one error type."""
    if not isinstance(value, str):
        raise ContentVersionError(
            "schema_violation", f"{description} must be a string, got {type(value)!r}"
        )
    text = unicodedata.normalize("NFC", value)
    try:
        grammar.reject_lone_surrogates(text)
    except FrameError as exc:
        raise ContentVersionError(exc.category, str(exc)) from exc
    return text


def _build_source_revision(entry: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(entry, Mapping):
        raise ContentVersionError(
            "schema_violation",
            f"source revision entry must be an object, got {type(entry)!r}",
        )
    _reject_unknown_members(entry, _SOURCE_REVISION_KEYS, "source revision entry")
    missing = _SOURCE_REVISION_KEYS - set(entry)
    if missing:
        raise ContentVersionError(
            "schema_violation",
            f"source revision entry is missing member(s): {sorted(missing)!r}",
        )
    return {
        "source": _nfc_string(entry["source"], description="source revision 'source'"),
        "revision": _nfc_string(
            entry["revision"], description="source revision 'revision'"
        ),
    }


def _build_facet(entry: Mapping[str, object]) -> dict[str, object]:
    """Build one closed `{axis, ref, basis}` facet entry.

    `ref` carries either the canonical facet ref string, or, for the unit
    axis once a conversion recipe has run, the canonical unit-request
    object. A facet ref digests a bound request, which may name a currency
    by role (`{"role": "observationAbout" | "origin" | "destination"}`) or
    as a concrete ISO code. This object is validated by
    `grammar.validate_unit_request_ref`, which accepts both forms. Frame's
    own `unit` cell, always already resolved to one concrete currency,
    uses `grammar.validate_unit_request` instead, which accepts ISO codes
    only. A unit-request object on any other axis is refused: no other axis
    has a request form to carry, so accepting one there would let an unrelated
    object silently pass as that axis's ref.
    """
    if not isinstance(entry, Mapping):
        raise ContentVersionError(
            "schema_violation", f"facet entry must be an object, got {type(entry)!r}"
        )
    _reject_unknown_members(entry, _FACET_KEYS, "facet entry")
    missing = _FACET_KEYS - set(entry)
    if missing:
        raise ContentVersionError(
            "schema_violation", f"facet entry is missing member(s): {sorted(missing)!r}"
        )

    axis = _nfc_string(entry["axis"], description="facet 'axis'")
    if axis not in _FACET_AXES:
        raise ContentVersionError(
            "invalid_facet_axis", f"unrecognised facet axis: {axis!r}"
        )

    basis = entry["basis"]
    # The isinstance check runs before the membership test: an unhashable
    # value (a JSON array or object) would make `in` on a frozenset raise a
    # raw TypeError instead of this refusal.
    if not isinstance(basis, str) or basis not in _FACET_BASES:
        raise ContentVersionError(
            "invalid_facet_basis",
            f"facet 'basis' must be 'bound' or 'declared_default', got {basis!r}",
        )

    ref = entry["ref"]
    ref_value: object
    if isinstance(ref, str):
        ref_value = _nfc_string(ref, description="facet 'ref'")
    elif isinstance(ref, Mapping):
        if axis != "unit":
            raise ContentVersionError(
                "invalid_facet_ref",
                f"a unit-request object is only valid on the unit axis, got axis {axis!r}",
            )
        if not all(isinstance(key, str) for key in ref):
            raise ContentVersionError(
                "schema_violation", "facet 'ref' request keys must be strings"
            )
        request = cast("Mapping[str, object]", ref)
        try:
            grammar.validate_unit_request_ref(request)
        except FrameError as exc:
            raise ContentVersionError(exc.category, str(exc)) from exc
        currency = request["currency"]
        built_currency: object
        if isinstance(currency, str):
            built_currency = unicodedata.normalize("NFC", currency)
        else:
            # The role shape: a closed one-member object whose 'role' value
            # is drawn from the spec's fixed role-key vocabulary, already
            # confirmed by validate_unit_request_ref -- carried verbatim,
            # since ASCII enum spellings have no non-canonical NFC form to
            # normalise away.
            role_currency = cast(Mapping[str, object], currency)
            built_currency = {"role": role_currency["role"]}
        built_request: dict[str, object] = {
            "currency": built_currency,
            "prices": request["prices"],
        }
        if "base_year" in request:
            built_request["base_year"] = request["base_year"]
        ref_value = built_request
    else:
        raise ContentVersionError(
            "schema_violation",
            f"facet 'ref' must be a string or mapping, got {type(ref)!r}",
        )

    return {"axis": axis, "ref": ref_value, "basis": basis}


def _conversion_trace_entry(
    entries: Sequence[object],
) -> Mapping[str, object]:
    """The one preimage-relevant entry of a manifest trace's `conversion`
    array, with its trace-only members dropped.

    A frame converts under one recipe and table-version pair, so its trace
    carries exactly one entry; any other length is refused rather than
    picked from or merged.
    """
    if len(entries) != 1:
        raise ContentVersionError(
            "schema_violation",
            f"a conversion array must hold exactly one entry, got {len(entries)}",
        )
    entry = entries[0]
    if not isinstance(entry, Mapping):
        raise ContentVersionError(
            "schema_violation",
            f"a conversion entry must be an object, got {type(entry)!r}",
        )
    entry = cast("Mapping[str, object]", entry)
    _reject_unknown_members(
        entry, _CONVERSION_KEYS | _CONVERSION_TRACE_ONLY_KEYS, "conversion entry"
    )
    return {key: value for key, value in entry.items() if key in _CONVERSION_KEYS}


def _build_conversion(conversion: object) -> dict[str, object]:
    """The preimage's closed `conversion` object, from either input spelling:
    the record itself, or a manifest trace's one-entry `conversion` array.
    Both spellings of the same record build the same object."""
    if isinstance(conversion, list | tuple):
        conversion = _conversion_trace_entry(conversion)
    elif isinstance(conversion, Mapping):
        conversion = cast("Mapping[str, object]", conversion)
        _reject_unknown_members(conversion, _CONVERSION_KEYS, "conversion")
    else:
        raise ContentVersionError(
            "schema_violation",
            "conversion must be an object or a one-entry array, "
            f"got {type(conversion)!r}",
        )
    missing = _CONVERSION_REQUIRED_KEYS - set(conversion)
    if missing:
        raise ContentVersionError(
            "schema_violation", f"conversion is missing member(s): {sorted(missing)!r}"
        )
    result: dict[str, object] = {
        "recipe": _nfc_string(conversion["recipe"], description="conversion 'recipe'"),
        "rate_table_version": _nfc_string(
            conversion["rate_table_version"],
            description="conversion 'rate_table_version'",
        ),
    }
    if "deflator_table_version" in conversion:
        result["deflator_table_version"] = _nfc_string(
            conversion["deflator_table_version"],
            description="conversion 'deflator_table_version'",
        )
    return result


def _reject_duplicate_key(
    entries: list[dict[str, object]], *, key: str, description: str
) -> None:
    """Refuse two entries sharing the same `key` value.

    One generation reads one revision per source, and one facet selection
    is made per axis, so two entries agreeing on `source` (or `axis`)
    describe the same slot twice -- contradictory input, not a tie a
    stable sort could silently order one way or the other. Refusing it
    here, before the array is sorted, is what keeps `source_revisions` and
    `facets` genuinely order-independent: a stable sort alone would still
    let two such inputs, differing only in which duplicate came first,
    mint two different tokens for the same set.
    """
    seen: set[object] = set()
    for entry in entries:
        value = entry[key]
        if value in seen:
            raise ContentVersionError(
                f"duplicate_{key}",
                f"two {description} entries share the same {key!r}: {value!r}",
            )
        seen.add(value)


def _build_preimage(
    *,
    frame_sha256: str,
    graph_revision: str,
    source_revisions: Iterable[Mapping[str, object]],
    facets: Iterable[Mapping[str, object]],
    conversion: ConversionInput,
) -> dict[str, object]:
    """The closed content_version preimage document, built from exactly the
    named arguments above and nothing else.

    `conversion` is omitted from the returned document entirely when
    `_NOT_GIVEN` (no recipe ran); an explicit `None` refuses instead of
    being treated as omission, so a caller cannot accidentally spell "no
    conversion" two different ways that would otherwise hash differently
    depending on which one it typed.
    """
    if not isinstance(frame_sha256, str) or not _FRAME_SHA256_RE.fullmatch(
        frame_sha256
    ):
        raise ContentVersionError(
            "invalid_digest",
            f"frame_sha256 must be 64 lowercase hex characters, got {frame_sha256!r}",
        )

    built_source_revisions = [
        _build_source_revision(entry) for entry in source_revisions
    ]
    _reject_duplicate_key(
        built_source_revisions, key="source", description="source_revisions"
    )
    built_facets = [_build_facet(entry) for entry in facets]
    _reject_duplicate_key(built_facets, key="axis", description="facets")

    preimage: dict[str, object] = {
        "identity_version": IDENTITY_VERSION,
        "frame_sha256": frame_sha256,
        "graph_revision": _nfc_string(graph_revision, description="graph_revision"),
        "source_revisions": sorted(
            built_source_revisions, key=lambda item: cast(str, item["source"])
        ),
        "facets": sorted(built_facets, key=lambda item: cast(str, item["axis"])),
    }

    if conversion is not _NOT_GIVEN:
        if conversion is None:
            raise ContentVersionError(
                "conversion_null",
                "conversion must be omitted when no recipe ran, not an explicit null",
            )
        preimage["conversion"] = _build_conversion(conversion)

    return preimage


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


def mint_content_version(
    *,
    frame_sha256: str,
    graph_revision: str,
    source_revisions: Iterable[Mapping[str, object]],
    facets: Iterable[Mapping[str, object]],
    conversion: ConversionInput = _NOT_GIVEN,
) -> str:
    """Mint a frame-level `content_version`: `cv_` plus the first 32
    lowercase hex characters of SHA-256 over the canonical (JCS) preimage
    bytes built from exactly these named arguments.

    `frame_sha256` is the full 64 lowercase hex digest of
    `dfo_frame_grammar.frame.canonical_frame_bytes(frame)` -- a nested
    digest, not the frame inline (see the module-level `frame_sha256`
    function above, which computes exactly this value). Every keyword
    argument here is the *complete* allowlist; there is no way to pass
    `spec_id`, `applied_window`, `materialiser_build`, or any other field
    only the wider manifest trace carries, because this function's
    signature has no parameter for it.

    `conversion` left at its default omits the `conversion` member from the
    preimage entirely -- the only spelling of "no recipe ran" this function
    accepts. An explicit `conversion=None` refuses instead of being treated
    as the same omission, so a caller can never accidentally spell "no
    conversion" two different ways that would otherwise hash differently
    depending on which one it typed. This is the opposite of
    `FrameTrace.conversion`, one level up, where `None` *is* the legitimate
    spelling of "no recipe ran" -- a plain dataclass field has no sentinel
    default to distinguish "omitted" from "explicitly null" the way a
    keyword argument can, so `recompute_content_version` is what translates
    `FrameTrace`'s `None` back into this function's omission convention
    before calling it.

    A present `conversion` is either the closed record (`recipe`,
    `rate_table_version`, optional `deflator_table_version`) or a manifest
    trace's `conversion` array holding exactly one entry. An entry carries
    the same three members plus the trace-only `members`, `fallback`, and
    `projected`, which are dropped before hashing, so both spellings of one
    record mint the same token. An array of any other length refuses as
    `schema_violation`.
    """
    preimage = _build_preimage(
        frame_sha256=frame_sha256,
        graph_revision=graph_revision,
        source_revisions=source_revisions,
        facets=facets,
        conversion=conversion,
    )
    canonical = canonicalize(preimage)
    digest = hashlib.sha256(canonical).hexdigest()
    return f"cv_{digest[:32]}"


@dataclass(frozen=True)
class FrameTrace:
    """The recompute witness for one materialised frame: exactly the
    lineage inputs `mint_content_version` hashed, plus
    `frame_grammar_version` for the trace's own benefit only.

    A plain dataclass, not a validating one -- it carries whatever its
    caller already validated when minting; `recompute_content_version`
    below is what re-runs the hash. `frame_grammar_version` is carried here
    because the manifest trace this fragment is embedded into needs it, but
    `recompute_content_version` never reads this field: a document-only
    grammar revision must never rotate a `content_version`, and the fastest
    way to prove that is a field the preimage builder cannot see.

    `conversion` takes either spelling `mint_content_version` accepts, and
    is `None` when no recipe ran -- here, unlike
    `mint_content_version`'s own `conversion` keyword one level up, `None`
    *is* the legitimate spelling of "no recipe ran": a dataclass field has
    no sentinel default of its own, so there is no second spelling for
    `recompute_content_version` to disambiguate against. It is
    `recompute_content_version` that bridges the two conventions, passing
    this field's default omission sentinel to `mint_content_version` when
    this is `None`, so that function's own explicit-`None`-refuses rule
    never fires for data that only ever passed through a `FrameTrace`.
    """

    graph_revision: str
    source_revisions: tuple[Mapping[str, object], ...]
    facets: tuple[Mapping[str, object], ...]
    frame_grammar_version: str
    conversion: Mapping[str, object] | Sequence[Mapping[str, object]] | None = None


def recompute_content_version(canonical_bytes: bytes, fragment: FrameTrace) -> str:
    """Recompute the `content_version` a stored frame and its `FrameTrace`
    fragment mint, as an integrity check.

    Hashes `canonical_bytes` exactly as given -- it is never re-parsed and
    the frame is never re-serialised from it -- so a mutated stored byte
    changes `frame_sha256` and is detected as a mismatch rather than
    silently laundered back to the original bytes by a round trip through
    the frame grammar. Callers therefore must persist the canonical frame
    bytes themselves, or a lossless form this library re-canonicalises back
    to the same bytes; anything else makes this function unable to do its
    job.
    """
    computed_frame_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    conversion: ConversionInput = (
        _NOT_GIVEN if fragment.conversion is None else fragment.conversion
    )
    return mint_content_version(
        frame_sha256=computed_frame_sha256,
        graph_revision=fragment.graph_revision,
        source_revisions=fragment.source_revisions,
        facets=fragment.facets,
        conversion=conversion,
    )
