"""Conformance harness: runs `dfo_frame_grammar` against its own golden vector
suite, shipped as package data at `src/dfo_frame_grammar/vectors/frame/*.json`
(see the README beside it for the record format, the three vector `kind`s,
and the frozen `failure_category` vocabulary negative vectors draw from).

Unlike `python/spec-kit/tests/test_identity_vectors.py`'s conformance
harness for the identity vectors, this suite ships inside this package
rather than behind an environment-variable lookup to a sibling checkout:
package data cannot go missing the way a sibling checkout can in a
worktree with no spec-kit checkout beside it, so there is no environment
gate here, and no skip path -- a missing or emptied vector directory is a
hard failure.

This module drives the real implementation against every vector and checks
its live output against that vector's own committed expectation -- a
different job from `tools/validate_frame_vectors.py`'s (which checks a
vector's own committed fields for internal consistency, without ever
importing this package; see that script's and the README's "Two
validators, two jobs"). `frame-vector.schema.json` is not loaded here for
the same reason: this module's job is running the implementation, not
re-validating fixture shape, which the standalone validator already owns.

This module also carries the vector digest guard (see
`TestVectorDigestGuard` below): a pinned constant over every vector's
canonical bytes and `content_version`, recomputed live from each vector's
own input on every run rather than read back from that vector's own stored
expectations, paired with a second pinned literal recording the
`FRAME_GRAMMAR_VERSION` in effect when the first was last hand-set. See
that class's docstring for exactly what this pairing does and does not
enforce.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json

import pytest

from dfo_frame_grammar.content_version import (
    ContentVersionError,
    mint_content_version,
)
from dfo_frame_grammar.frame import Frame, canonical_frame_bytes
from dfo_frame_grammar.grammar import FrameError
from dfo_frame_grammar.version import FRAME_GRAMMAR_VERSION

_MIN_VECTOR_FLOOR = 20


def _vector_paths() -> list[importlib.resources.abc.Traversable]:
    root = importlib.resources.files("dfo_frame_grammar") / "vectors" / "frame"
    return sorted(
        (
            entry
            for entry in root.iterdir()
            # frame-vector.schema.json lives alongside the vectors it
            # validates (see the README's "ships as package data" note) --
            # not itself a vector, so excluded from the suite this harness
            # parametrizes over.
            if entry.name.endswith(".json") and entry.name != "frame-vector.schema.json"
        ),
        key=lambda entry: entry.name,
    )


def _load_vector(entry: importlib.resources.abc.Traversable) -> dict:
    with entry.open("rb") as f:
        return json.load(f)


_VECTOR_PATHS = _vector_paths()

if not _VECTOR_PATHS:
    # Package data, not a sibling checkout: there is no environment
    # variable that could plausibly explain zero vectors here, so this is
    # always a hard failure, never a skip -- see this module's own
    # docstring.
    raise RuntimeError(
        "no frame grammar vectors found under dfo_frame_grammar/vectors/frame -- "
        "the vector suite is missing from this package's own data, not a "
        "sibling checkout that could plausibly be absent"
    )

_VECTORS = [_load_vector(path) for path in _VECTOR_PATHS]


def test_suite_is_non_empty_and_meets_the_drafted_floor() -> None:
    """A truncated or emptied vector directory must fail loudly, never
    silently pass with zero (or too few) vectors exercised."""
    assert len(_VECTORS) > 0, "no frame grammar vectors found"
    assert len(_VECTORS) >= _MIN_VECTOR_FLOOR, (
        f"expected at least {_MIN_VECTOR_FLOOR} vectors, found {len(_VECTORS)} "
        "-- the vector directory may be truncated"
    )


def _run_frame_vector(vector: dict) -> None:
    """A negative `"frame"` vector is refused during frame construction
    itself, before any `content_version` preimage is ever built -- see the
    README's "kind: frame" section -- so a negative vector's own
    `failure_category` is checked against `Frame.build` alone, never
    against minting."""
    if vector["classification"] == "negative":
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=vector["role_keys"], rows=vector["rows"])
        assert excinfo.value.category == vector["failure_category"]
        return

    frame = Frame.build(role_keys=vector["role_keys"], rows=vector["rows"])
    assert list(frame.columns) == vector["expected_columns"]

    canonical_bytes = canonical_frame_bytes(frame)
    assert canonical_bytes == bytes.fromhex(vector["canonical_bytes"])

    frame_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    assert frame_sha256 == vector["expected_frame_sha256"]

    content_version = mint_content_version(
        frame_sha256=frame_sha256, **vector["content_version_input"]
    )
    assert content_version == vector["expected_content_version"]


def _run_content_version_vector(vector: dict) -> None:
    """A `"content_version"` vector supplies `frame_sha256` directly (see
    the README's "kind: content_version" section) rather than deriving it
    from a frame, so a preimage-level refusal can be exercised without an
    otherwise-valid frame just to reach it."""
    if vector["classification"] == "negative":
        with pytest.raises(ContentVersionError) as excinfo:
            mint_content_version(
                frame_sha256=vector["frame_sha256"], **vector["content_version_input"]
            )
        assert excinfo.value.category == vector["failure_category"]
        return

    content_version = mint_content_version(
        frame_sha256=vector["frame_sha256"], **vector["content_version_input"]
    )
    assert content_version == vector["expected_content_version"]


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["name"] for v in _VECTORS])
def test_frame_grammar_vector(vector: dict) -> None:
    if vector["kind"] == "frame":
        _run_frame_vector(vector)
    elif vector["kind"] == "content_version":
        _run_content_version_vector(vector)
    else:
        pytest.fail(f"unrecognised vector kind: {vector['kind']!r}")


def test_suite_carries_both_classifications() -> None:
    """A refusal-only or a success-only suite would leave half of what this
    grammar pins untested by a second implementation -- see the README's
    "The positive/negative split"."""
    classifications = {vector["classification"] for vector in _VECTORS}
    assert classifications == {"positive", "negative"}, (
        f"expected both 'positive' and 'negative' vectors, found {classifications}"
    )


class TestConvergencePairs:
    """The vector suite's own deliberate convergence pairs (see the
    README's "Convergence pairs" section) -- checked here as an explicit
    cross-vector assertion, not just implied by two files independently
    matching their own expectations."""

    def _by_name(self, name: str) -> dict:
        for vector in _VECTORS:
            if vector["name"] == name:
                return vector
        raise AssertionError(f"vector {name!r} not found in the loaded suite")

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("frame-row-order-input-converges", "frame-row-order-shuffled-converges"),
            (
                "frame-value-int-spelling-converges",
                "frame-value-float-spelling-converges",
            ),
            (
                "frame-unicode-decomposed-converges",
                "frame-unicode-precomposed-converges",
            ),
            (
                "frame-multi-role-unicode-order",
                "frame-row-key-decomposed-spelling-converges",
            ),
        ],
    )
    def test_frame_pair_shares_bytes_and_cv(self, first: str, second: str) -> None:
        a, b = self._by_name(first), self._by_name(second)
        # json.dumps, not Python `!=`: Python considers 1000 == 1000.0, which
        # would make the int/float spelling pair look structurally identical
        # by that comparison even though the two vectors are typed
        # differently at the JSON boundary -- exactly the distinction this
        # pair exists to prove converges downstream.
        assert json.dumps(a["rows"]) != json.dumps(b["rows"])
        assert a["canonical_bytes"] == b["canonical_bytes"]
        assert a["expected_content_version"] == b["expected_content_version"]

    def test_multi_source_multi_facet_pair_shares_cv_despite_differing_array_order(
        self,
    ) -> None:
        # Unlike the frame pairs above, this pair's `rows` are identical --
        # what differs is `source_revisions` and `facets` order inside
        # `content_version_input`, the two arrays this suite's own ordering
        # rule pins. The vectors' own `expected_content_version` values
        # converging is the property this pair exists to prove.
        a = self._by_name("frame-multi-source-multi-facet-input-order-converges")
        b = self._by_name("frame-multi-source-multi-facet-shuffled-converges")
        assert (
            a["content_version_input"]["source_revisions"]
            != b["content_version_input"]["source_revisions"]
        )
        assert (
            a["content_version_input"]["facets"] != b["content_version_input"]["facets"]
        )
        assert a["expected_content_version"] == b["expected_content_version"]


# ---------------------------------------------------------------------------
# The vector digest guard
# ---------------------------------------------------------------------------


def _compute_vector_digest_guard() -> str:
    """SHA-256 over every *positive* vector's canonical bytes and
    `content_version`, *recomputed live from each vector's own input*
    (`role_keys`/`rows`/`content_version_input`, `frame_sha256`, or
    `members`) through the real implementation, sorted by vector name so
    the digest is independent of directory iteration order.

    Negative vectors are excluded: they have no canonical bytes or
    `content_version` of their own to pin -- a refusal's expected shape is
    its `failure_category`, already checked live, once, by
    `test_frame_grammar_vector`, not an output byte sequence that could
    silently drift the way this guard exists to catch.

    Deliberately recomputed from each vector's input rather than read back
    from that same vector's own `canonical_bytes`/`expected_content_version`
    fields: reading the stored fields would make this indistinguishable
    from re-hashing the vector files themselves, and could not, on its
    own, catch a code regression that a matching (and equally wrong)
    update to the vector files' own stored fields had papered over.
    Recomputing from input means this guard depends only on the vector
    inputs and the code, not on whatever a committed file happens to claim
    its own output is -- the same reason `test_frame_grammar_vector` above
    checks live output against the committed fields in the first place,
    just aggregated into one digest checked against a literal pinned in
    this file rather than in the vector data.
    """
    parts: list[str] = []
    for vector in sorted(_VECTORS, key=lambda v: v["name"]):
        if vector["classification"] == "negative":
            continue
        if vector["kind"] == "frame":
            frame = Frame.build(role_keys=vector["role_keys"], rows=vector["rows"])
            canonical_bytes = canonical_frame_bytes(frame)
            frame_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
            content_version = mint_content_version(
                frame_sha256=frame_sha256, **vector["content_version_input"]
            )
            parts.append(f"{vector['name']}:{canonical_bytes.hex()}:{content_version}")
        elif vector["kind"] == "content_version":
            content_version = mint_content_version(
                frame_sha256=vector["frame_sha256"], **vector["content_version_input"]
            )
            parts.append(f"{vector['name']}:{content_version}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# Recomputed and compared against on every test run (see
# `TestVectorDigestGuard.test_guard_matches_the_live_recomputation` below).
# Regenerate with `_compute_vector_digest_guard()` whenever the vector suite
# changes -- adding a vector moves this digest legitimately, with no
# `FRAME_GRAMMAR_VERSION` bump required, precisely because a new vector's
# own name and its already-correct expected output enter the digest's input
# without any existing vector's expected output moving. Only bump
# `FRAME_GRAMMAR_VERSION` (`dfo_frame_grammar.version`) and
# `_VECTOR_DIGEST_GUARD_FRAME_GRAMMAR_VERSION` below, in the same change,
# when regenerating this digest because an *already-published* vector's own
# expected `canonical_bytes` or `content_version` changed -- the byte-rule
# regression case this guard exists to catch.
_VECTOR_DIGEST_GUARD = (
    "aa0309bb28188daa25991c7fab7bc081093f986277cdeefcb1e292c0a0a2eeb9"
)

# The FRAME_GRAMMAR_VERSION in effect when _VECTOR_DIGEST_GUARD above was
# last hand-set. See TestVectorDigestGuard for what asserting equality
# against the live FRAME_GRAMMAR_VERSION does, and does not, enforce.
_VECTOR_DIGEST_GUARD_FRAME_GRAMMAR_VERSION = "1.0.0"


class TestVectorDigestGuard:
    """A drift guard: a pinned digest over every vector's canonical bytes
    and `content_version`, *recomputed live from each vector's own input*
    through the real implementation on every test run, paired with a
    second pinned literal recording which `FRAME_GRAMMAR_VERSION` was
    current when the digest was last hand-set.

    What this genuinely proves: `test_guard_matches_the_live_recomputation`
    recomputes every vector's canonical bytes and `content_version` from
    that vector's own input (never from its own stored expected fields --
    see `_compute_vector_digest_guard`'s docstring) and compares the
    aggregate digest against the literal pinned above. Because the pinned
    literal lives in this test file, not in a vector's own JSON, a genuine
    byte-rule regression moves this digest exactly when it also moves
    `test_frame_grammar_vector`'s per-vector comparisons -- both fail
    together whether or not anyone has (correctly or incorrectly) also
    edited a vector file's own `canonical_bytes`/`expected_content_version`
    fields to match the regression, since this guard never reads those
    fields at all. Legitimately adding a new vector also moves this digest
    -- its name and (already-correct) expected output become new input to
    the aggregate hash -- with no byte-rule regression and no
    `FRAME_GRAMMAR_VERSION` bump implied; only an *existing* vector's own
    expected output moving is the regression case this guard is for.

    What this does **not** prove, and is not claimed to:
    `test_guard_is_paired_with_the_live_grammar_version` only asserts that
    two literals living in this same file --
    `_VECTOR_DIGEST_GUARD_FRAME_GRAMMAR_VERSION` here and
    `FRAME_GRAMMAR_VERSION` in `dfo_frame_grammar.version` -- agree with each
    other right now. Pytest has no visibility into git history, so it
    cannot confirm that the last change to `_VECTOR_DIGEST_GUARD` landed in
    the identical commit as a `FRAME_GRAMMAR_VERSION` bump; it can only
    fail if someone updates one pinned literal without updating the other
    to match. A contributor who deliberately changes a byte rule, updates
    `_VECTOR_DIGEST_GUARD` to the newly recomputed digest, and *also*
    bumps `_VECTOR_DIGEST_GUARD_FRAME_GRAMMAR_VERSION` to some string that
    happens to equal `FRAME_GRAMMAR_VERSION` without having actually
    changed `FRAME_GRAMMAR_VERSION` in `dfo_frame_grammar.version` would pass
    both tests here without ever having bumped the real version. Closing
    that gap needs a diff-aware check (a pre-commit hook or a CI step that
    inspects which files a commit touched), not a pytest assertion running
    against a single checked-out tree -- this class does not claim to be
    that check.
    """

    def test_guard_matches_the_live_recomputation(self) -> None:
        assert _compute_vector_digest_guard() == _VECTOR_DIGEST_GUARD, (
            "the frame grammar vector suite's live-recomputed canonical bytes "
            "and content_version no longer match the pinned _VECTOR_DIGEST_GUARD "
            "-- if this is a deliberate behaviour change, regenerate the guard "
            "constant AND bump FRAME_GRAMMAR_VERSION (dfo_frame_grammar.version) "
            "in the same change"
        )

    def test_guard_is_paired_with_the_live_grammar_version(self) -> None:
        assert _VECTOR_DIGEST_GUARD_FRAME_GRAMMAR_VERSION == FRAME_GRAMMAR_VERSION, (
            "_VECTOR_DIGEST_GUARD_FRAME_GRAMMAR_VERSION no longer matches the "
            "live FRAME_GRAMMAR_VERSION -- update the pinned literal here "
            "alongside whatever bumped the real version"
        )
