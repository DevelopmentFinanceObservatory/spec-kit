"""`content_version` minting and recompute -- the preimage rules from
`docs/frame-grammar-v1.md`.

Every token here is proved by perturbation: a rule is exercised by changing
one input and observing whether the minted token moves, never by asserting
a docstring's claim in isolation.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import subprocess
import sys

import pytest
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar.content_version import (
    IDENTITY_VERSION,
    ContentVersionError,
    _build_preimage,
    frame_sha256,
    mint_content_version,
    recompute_content_version,
)
from dfo_frame_grammar.frame import Frame, canonical_frame_bytes

_FRAME_SHA256_A = hashlib.sha256(b"frame-a").hexdigest()
_FRAME_SHA256_B = hashlib.sha256(b"frame-b").hexdigest()


def _mint(**overrides: object) -> str:
    kwargs: dict[str, object] = {"frame_sha256": _FRAME_SHA256_A}
    kwargs.update(overrides)
    return mint_content_version(**kwargs)


def _token(preimage: dict[str, object]) -> str:
    return f"cv_{hashlib.sha256(canonicalize(preimage)).hexdigest()[:32]}"


class TestPreimage:
    def test_key_set_is_the_separator_and_the_frame_digest(self):
        preimage = _build_preimage(frame_sha256=_FRAME_SHA256_A)
        assert preimage == {"identity_version": "cv2", "frame_sha256": _FRAME_SHA256_A}

    def test_token_is_the_truncated_digest_of_the_jcs_preimage(self):
        assert _mint() == _token(
            {"identity_version": "cv2", "frame_sha256": _FRAME_SHA256_A}
        )

    def test_separator_differs_from_the_lineage_family(self):
        # `cv1` is the retired family whose preimage also hashed lineage. A
        # distinct separator keeps a `cv2` token from ever equalling a `cv1`
        # token minted over a different preimage.
        assert IDENTITY_VERSION == "cv2"
        assert _mint() != _token(
            {"identity_version": "cv1", "frame_sha256": _FRAME_SHA256_A}
        )


class TestLineageExclusion:
    """The preimage is the frame digest alone. Neither function accepts a
    lineage or request field, so one frame minted under any lineage, or for
    any definition, is one `content_version`."""

    def test_mint_accepts_the_frame_digest_only(self):
        assert list(inspect.signature(mint_content_version).parameters) == [
            "frame_sha256"
        ]

    def test_recompute_accepts_the_frame_bytes_only(self):
        assert list(inspect.signature(recompute_content_version).parameters) == [
            "canonical_bytes"
        ]

    @pytest.mark.parametrize(
        "field",
        [
            "graph_revision",
            "source_revisions",
            "facets",
            "conversion",
            "spec_id",
            "applied_window",
            "coverage",
            "materialiser_build",
            "default_policy",
            "default_policy_version",
            "selection_reason",
            "dispatch_facet_id",
            "frame_grammar_version",
        ],
    )
    def test_field_is_not_an_accepted_parameter(self, field):
        with pytest.raises(TypeError):
            _mint(**{field: "some-value"})


class TestFrameSha256:
    def test_perturbing_frame_sha256_moves_the_cv(self):
        assert _mint(frame_sha256=_FRAME_SHA256_A) != _mint(
            frame_sha256=_FRAME_SHA256_B
        )

    def test_wrong_length_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(frame_sha256="ab" * 10)
        assert excinfo.value.category == "invalid_digest"

    def test_uppercase_hex_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(frame_sha256=_FRAME_SHA256_A.upper())
        assert excinfo.value.category == "invalid_digest"

    def test_a_32_hex_public_token_shaped_string_refuses(self):
        # A caller who passes a public token's own 32-hex digest slice
        # (the shape a `spec_`/`cv_` token's suffix carries) where the full
        # 64-hex frame digest belongs must refuse, not mint over a
        # truncated, non-recomputable value.
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(frame_sha256=_FRAME_SHA256_A[:32])
        assert excinfo.value.category == "invalid_digest"

    def test_non_hex_characters_refuse(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(frame_sha256="g" * 64)
        assert excinfo.value.category == "invalid_digest"

    def test_non_string_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(frame_sha256=None)
        assert excinfo.value.category == "invalid_digest"


class TestFrameSha256Helper:
    """`frame_sha256(frame)`, the exported helper composing
    `canonical_frame_bytes` and SHA-256 so a caller never hand-rolls the
    digest `mint_content_version`'s own `frame_sha256` keyword requires."""

    def test_matches_the_hand_rolled_digest(self):
        frame = Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:Count",
                    "provider": "country/FRA",
                    "time_period": "2020",
                    "value": 1.0,
                }
            ],
        )
        assert (
            frame_sha256(frame)
            == hashlib.sha256(canonical_frame_bytes(frame)).hexdigest()
        )

    def test_output_is_accepted_by_mint_content_version(self):
        frame = Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:Count",
                    "provider": "country/FRA",
                    "time_period": "2020",
                    "value": 1.0,
                }
            ],
        )
        cv = _mint(frame_sha256=frame_sha256(frame))
        assert cv.startswith("cv_")


class TestRecompute:
    def test_recompute_over_the_stored_bytes_matches_the_minted_cv(self):
        canonical_bytes = b"the exact stored canonical frame bytes"
        minted = _mint(frame_sha256=hashlib.sha256(canonical_bytes).hexdigest())
        assert recompute_content_version(canonical_bytes) == minted

    def test_mutating_one_stored_byte_is_detected_as_a_mismatch(self):
        canonical_bytes = b"the exact stored canonical frame bytes"
        minted = _mint(frame_sha256=hashlib.sha256(canonical_bytes).hexdigest())
        mutated = bytearray(canonical_bytes)
        mutated[0] ^= 0xFF
        assert recompute_content_version(bytes(mutated)) != minted


_DETERMINISM_SCRIPT = """
from dfo_frame_grammar.content_version import frame_sha256, mint_content_version
from dfo_frame_grammar.frame import Frame, canonical_frame_bytes

frame = Frame.build(
    role_keys=["provider", "recipient"],
    rows=[
        {
            "stat_var": "dcid:MalariaGrantDisbursements",
            "provider": "country/FRA",
            "recipient": "country/TGO",
            "time_period": "2020",
            "value": 1.0,
            "measurementMethod": "M1",
            "provenance": "P1",
        },
        {
            "stat_var": "dcid:MalariaGrantDisbursements",
            "provider": "country/DEU",
            "recipient": "country/TGO",
            "time_period": "2020",
            "value": 2.0,
            "measurementMethod": "M2",
            "provenance": "P2",
        },
        {
            "stat_var": "dcid:MalariaGrantDisbursements",
            "provider": "country/FRA",
            "recipient": "country/USA",
            "time_period": "2021",
            "value": 3.0,
        },
    ],
)
canonical_bytes = canonical_frame_bytes(frame)
digest = frame_sha256(frame)
content_version = mint_content_version(frame_sha256=digest)
print(canonical_bytes.hex())
print(content_version)
"""


class TestCrossProcessDeterminism:
    """Nothing about `hashlib.sha256`, JCS canonicalisation, or this
    module's own sorting proves stable across a fresh interpreter or a
    different `PYTHONHASHSEED` -- Python's own hash randomisation affects
    dict and set iteration order, and a preimage built by iterating a dict
    would be exactly the kind of defect this proves absent."""

    def test_canonical_bytes_and_cv_are_stable_across_hash_seeds(self):
        outputs = []
        for seed in ("0", "1"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run(
                [sys.executable, "-c", _DETERMINISM_SCRIPT],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            outputs.append(result.stdout.strip().splitlines())

        assert outputs[0] == outputs[1]
        canonical_hex, content_version = outputs[0]
        assert len(canonical_hex) > 0
        assert content_version.startswith("cv_")
