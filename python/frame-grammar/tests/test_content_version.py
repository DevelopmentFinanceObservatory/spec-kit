"""`content_version` minting, recompute, the collection-level token, and
`FrameTrace` -- the pinned preimage rules from `docs/frame-grammar-v1.md`.

Every token here is proved by perturbation: a rule is exercised by changing
one input and observing whether the minted token moves, never by asserting
a docstring's claim in isolation.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import pytest

from dfo_frame_grammar.content_version import (
    _NOT_GIVEN,
    ContentVersionError,
    FrameTrace,
    _build_preimage,
    frame_sha256,
    mint_collection_content_version,
    mint_content_version,
    recompute_content_version,
)
from dfo_frame_grammar.frame import Frame, canonical_frame_bytes

_FRAME_SHA256_A = hashlib.sha256(b"frame-a").hexdigest()
_FRAME_SHA256_B = hashlib.sha256(b"frame-b").hexdigest()

_SOURCE_REVISIONS = [{"source": "OECD_DAC_CRS", "revision": "rev-1"}]
_FACETS = [{"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "declared_default"}]

# Well-formed collection member tokens: `spec_`/`cv_` plus exactly 32
# lowercase hex characters, the shape `mint_collection_content_version`
# requires of every member.
_SPEC_A = "spec_" + "a" * 32
_SPEC_B = "spec_" + "b" * 32
_SPEC_C = "spec_" + "c" * 32
_MEMBER_CV_1 = "cv_" + "1" * 32
_MEMBER_CV_2 = "cv_" + "2" * 32
_MEMBER_CV_3 = "cv_" + "3" * 32
_MEMBER_CV_9 = "cv_" + "9" * 32


def _mint(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "frame_sha256": _FRAME_SHA256_A,
        "graph_revision": "graph-rev-1",
        "source_revisions": _SOURCE_REVISIONS,
        "facets": _FACETS,
    }
    kwargs.update(overrides)
    return mint_content_version(**kwargs)


class TestPreimageKeySet:
    def test_omitted_conversion_key_set(self):
        preimage = _build_preimage(
            frame_sha256=_FRAME_SHA256_A,
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
            conversion=_NOT_GIVEN,
        )
        assert set(preimage) == {
            "identity_version",
            "frame_sha256",
            "graph_revision",
            "source_revisions",
            "facets",
        }

    def test_present_conversion_key_set(self):
        preimage = _build_preimage(
            frame_sha256=_FRAME_SHA256_A,
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
            conversion={"recipe": "fx_convert", "rate_table_version": "rt-1"},
        )
        assert set(preimage) == {
            "identity_version",
            "frame_sha256",
            "graph_revision",
            "source_revisions",
            "facets",
            "conversion",
        }
        assert preimage["identity_version"] == "cv1"


class TestPinnedExclusions:
    """Each field the preimage's closed member set omits from a
    content_version.
    """

    @pytest.mark.parametrize(
        "field",
        [
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
        # mint_content_version's signature IS the closed allowlist: passing
        # any of these raises TypeError before a single byte is hashed,
        # which is what makes the exclusion structural rather than a
        # discipline someone could forget to uphold.
        with pytest.raises(TypeError):
            _mint(**{field: "some-value"})

    def test_distinct_spec_ids_over_the_same_frame_and_lineage_mint_the_same_cv(self):
        # spec_id is not a parameter at all, so two callers minting for
        # distinct definitions ("spec_aaa...", "spec_bbb...") over identical
        # lineage necessarily produce the same token -- (spec_id,
        # content_version) names a logical result, not a byte sequence.
        cv_for_spec_a = _mint()
        cv_for_spec_b = _mint()
        assert cv_for_spec_a == cv_for_spec_b

    def test_bumping_frame_grammar_version_alone_leaves_the_recomputed_cv_standing_still(
        self,
    ):
        canonical_bytes = b"stored canonical frame bytes"
        fragment_v1 = FrameTrace(
            graph_revision="graph-rev-1",
            source_revisions=tuple(_SOURCE_REVISIONS),
            facets=tuple(_FACETS),
            frame_grammar_version="frame1.0.0",
        )
        fragment_v2 = FrameTrace(
            graph_revision="graph-rev-1",
            source_revisions=tuple(_SOURCE_REVISIONS),
            facets=tuple(_FACETS),
            frame_grammar_version="frame1.1.0",
        )
        assert fragment_v1.frame_grammar_version != fragment_v2.frame_grammar_version
        assert recompute_content_version(
            canonical_bytes, fragment_v1
        ) == recompute_content_version(canonical_bytes, fragment_v2)


class TestGraphRevision:
    def test_perturbing_graph_revision_moves_the_cv(self):
        assert _mint(graph_revision="graph-rev-1") != _mint(
            graph_revision="graph-rev-2"
        )

    def test_frame_digest_stands_still_when_only_graph_revision_moves(self):
        # frame_sha256 is a caller-supplied argument, independent of
        # graph_revision -- proving it stands still is proving the two are
        # hashed as unrelated preimage members, not derived from one another.
        preimage_a = _build_preimage(
            frame_sha256=_FRAME_SHA256_A,
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
            conversion=_NOT_GIVEN,
        )
        preimage_b = _build_preimage(
            frame_sha256=_FRAME_SHA256_A,
            graph_revision="graph-rev-2",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
            conversion=_NOT_GIVEN,
        )
        assert preimage_a["graph_revision"] != preimage_b["graph_revision"]
        assert preimage_a["frame_sha256"] == preimage_b["frame_sha256"]


class TestSourceRevisions:
    def test_perturbing_one_revision_moves_the_cv(self):
        base = [{"source": "OECD_DAC_CRS", "revision": "rev-1"}]
        perturbed = [{"source": "OECD_DAC_CRS", "revision": "rev-2"}]
        assert _mint(source_revisions=base) != _mint(source_revisions=perturbed)

    def test_reordering_the_input_array_mints_the_same_cv(self):
        forward = [
            {"source": "OECD_DAC_CRS", "revision": "rev-1"},
            {"source": "WORLD_BANK_WDI", "revision": "rev-2"},
        ]
        reversed_order = list(reversed(forward))
        assert forward != reversed_order
        assert _mint(source_revisions=forward) == _mint(source_revisions=reversed_order)

    def test_unknown_member_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(source_revisions=[{"source": "S", "revision": "r", "extra": "x"}])
        assert excinfo.value.category == "unknown_member"

    def test_missing_member_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(source_revisions=[{"source": "S"}])
        assert excinfo.value.category == "schema_violation"

    def test_two_entries_sharing_a_source_refuse(self):
        # One generation reads one revision per source, so two entries
        # agreeing on `source` describe the same slot twice -- contradictory
        # input, refused rather than tie-broken by a stable sort.
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                source_revisions=[
                    {"source": "OECD_DAC_CRS", "revision": "rev-1"},
                    {"source": "OECD_DAC_CRS", "revision": "rev-2"},
                ]
            )
        assert excinfo.value.category == "duplicate_source"

    def test_two_entries_colliding_on_source_only_after_nfc_refuse(self):
        # Byte-distinct as typed, but the same source once both are
        # NFC-normalised -- proves the duplicate check runs on the built,
        # NFC-normalised entries rather than on the raw input strings.
        decomposed = "café"  # "café" spelled with a combining acute accent
        precomposed = "café"  # the same word, NFC-composed
        assert decomposed != precomposed
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                source_revisions=[
                    {"source": decomposed, "revision": "rev-1"},
                    {"source": precomposed, "revision": "rev-2"},
                ]
            )
        assert excinfo.value.category == "duplicate_source"


class TestFacets:
    def test_flipping_basis_between_bound_and_declared_default_moves_the_cv(self):
        bound = [{"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "bound"}]
        default = [
            {"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "declared_default"}
        ]
        assert _mint(facets=bound) != _mint(facets=default)

    def test_perturbing_ref_moves_the_cv(self):
        a = [{"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "bound"}]
        b = [{"axis": "provenance", "ref": "WORLD_BANK_WDI", "basis": "bound"}]
        assert _mint(facets=a) != _mint(facets=b)

    def test_reordering_the_input_array_mints_the_same_cv(self):
        forward = [
            {"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "bound"},
            {"axis": "measurementMethod", "ref": "GrantEquivalent", "basis": "bound"},
        ]
        reversed_order = list(reversed(forward))
        assert forward != reversed_order
        assert _mint(facets=forward) == _mint(facets=reversed_order)

    def test_unit_axis_ref_accepts_a_unit_request_object(self):
        cv = _mint(
            facets=[
                {
                    "axis": "unit",
                    "ref": {"currency": "USD", "prices": "constant", "base_year": 2020},
                    "basis": "bound",
                }
            ]
        )
        assert cv.startswith("cv_")

    def test_invalid_unit_request_ref_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "unit",
                        "ref": {
                            "currency": "USD",
                            "prices": "constant",
                        },  # missing base_year
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_unit"

    def test_invalid_basis_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "guessed"}
                ]
            )
        assert excinfo.value.category == "invalid_facet_basis"

    def test_basis_as_a_list_refuses_without_raising_a_type_error(self):
        # The isinstance check must run before the frozenset membership
        # test: an unhashable 'basis' would otherwise raise a raw
        # TypeError instead of this refusal.
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": ["bound"]}
                ]
            )
        assert excinfo.value.category == "invalid_facet_basis"

    def test_unrecognised_axis_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(facets=[{"axis": "made_up_axis", "ref": "x", "basis": "bound"}])
        assert excinfo.value.category == "invalid_facet_axis"

    def test_unknown_member_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {"axis": "provenance", "ref": "x", "basis": "bound", "extra": "y"}
                ]
            )
        assert excinfo.value.category == "unknown_member"

    def test_two_entries_sharing_an_axis_refuse(self):
        # One facet selection is made per axis, so two entries agreeing on
        # `axis` describe the same selection twice -- contradictory input,
        # refused rather than tie-broken by a stable sort.
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {"axis": "provenance", "ref": "OECD_DAC_CRS", "basis": "bound"},
                    {"axis": "provenance", "ref": "WORLD_BANK_WDI", "basis": "bound"},
                ]
            )
        assert excinfo.value.category == "duplicate_axis"

    def test_unit_request_ref_on_provenance_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "provenance",
                        "ref": {"currency": "USD", "prices": "current"},
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_facet_ref"

    def test_unit_request_ref_on_measurement_method_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "measurementMethod",
                        "ref": {"currency": "USD", "prices": "current"},
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_facet_ref"

    def test_the_same_unit_request_object_on_the_unit_axis_still_mints(self):
        request = {"currency": "USD", "prices": "current"}
        cv = _mint(facets=[{"axis": "unit", "ref": request, "basis": "bound"}])
        assert cv.startswith("cv_")

    @pytest.mark.parametrize("role", ["observationAbout", "origin", "destination"])
    def test_unit_axis_ref_accepts_a_role_shaped_currency(self, role):
        # A content-version facet ref digests a *bound* request, which may
        # still name a currency by role rather than one already resolved
        # per row -- unlike the frame's own per-row `unit` cell.
        cv = _mint(
            facets=[
                {
                    "axis": "unit",
                    "ref": {"currency": {"role": role}, "prices": "current"},
                    "basis": "bound",
                }
            ]
        )
        assert cv.startswith("cv_")

    def test_perturbing_the_role_moves_the_cv(self):
        origin = _mint(
            facets=[
                {
                    "axis": "unit",
                    "ref": {"currency": {"role": "origin"}, "prices": "current"},
                    "basis": "bound",
                }
            ]
        )
        destination = _mint(
            facets=[
                {
                    "axis": "unit",
                    "ref": {"currency": {"role": "destination"}, "prices": "current"},
                    "basis": "bound",
                }
            ]
        )
        assert origin != destination

    def test_role_shaped_and_string_shaped_currency_mint_different_tokens(self):
        role = _mint(
            facets=[
                {
                    "axis": "unit",
                    "ref": {"currency": {"role": "origin"}, "prices": "current"},
                    "basis": "bound",
                }
            ]
        )
        string = _mint(
            facets=[
                {
                    "axis": "unit",
                    "ref": {"currency": "USD", "prices": "current"},
                    "basis": "bound",
                }
            ]
        )
        assert role != string

    def test_unit_axis_ref_refuses_an_unrecognised_role(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "unit",
                        "ref": {
                            "currency": {"role": "financingProvider"},
                            "prices": "current",
                        },
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_unit"

    def test_unit_axis_ref_refuses_a_role_object_with_an_extra_member(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "unit",
                        "ref": {
                            "currency": {"role": "origin", "extra": "x"},
                            "prices": "current",
                        },
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_unit"

    def test_unit_axis_ref_refuses_a_lowercase_role(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "unit",
                        "ref": {
                            "currency": {"role": "observationabout"},
                            "prices": "current",
                        },
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_unit"


class TestConversionSlot:
    def test_adding_a_conversion_record_moves_the_cv(self):
        without = _mint()
        with_recipe = _mint(
            conversion={"recipe": "fx_convert", "rate_table_version": "rt-1"}
        )
        assert without != with_recipe

    def test_explicit_null_conversion_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion=None)
        assert excinfo.value.category == "conversion_null"

    def test_deflator_present_versus_absent_mint_different_tokens(self):
        no_deflator = _mint(
            conversion={"recipe": "fx_convert", "rate_table_version": "rt-1"}
        )
        with_deflator = _mint(
            conversion={
                "recipe": "fx_convert",
                "rate_table_version": "rt-1",
                "deflator_table_version": "dt-1",
            }
        )
        assert no_deflator != with_deflator

    def test_unknown_member_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                conversion={
                    "recipe": "fx_convert",
                    "rate_table_version": "rt-1",
                    "extra": "x",
                }
            )
        assert excinfo.value.category == "unknown_member"

    def test_missing_required_member_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion={"recipe": "fx_convert"})
        assert excinfo.value.category == "schema_violation"


_RECORD = {
    "recipe": "fx_deflate_convert",
    "rate_table_version": "rt-1",
    "deflator_table_version": "dt-1",
}
# The shape a manifest trace's `conversion` entry carries: the three preimage
# members plus the trace-only `members`, `fallback`, and `projected`.
_TRACE_ENTRY = {
    **_RECORD,
    "members": [{"stat_var": "dcid:sv_a", "native_unit": "dcid:USD"}],
    "fallback": {"count": 0, "examples": [], "truncated": False},
    "projected": {"count": 2, "examples": ["2025"], "truncated": False},
}


class TestConversionTraceEntries:
    @pytest.mark.parametrize(
        "entries",
        [[_TRACE_ENTRY], (_TRACE_ENTRY,), [_RECORD]],
        ids=["trace-entry-list", "trace-entry-tuple", "bare-record-list"],
    )
    def test_a_one_entry_array_mints_the_same_cv_as_the_record(self, entries):
        assert _mint(conversion=entries) == _mint(conversion=_RECORD)

    def test_an_entry_without_a_deflator_matches_the_record_without_one(self):
        record = {"recipe": "fx_convert", "rate_table_version": "rt-1"}
        entry = {**record, "members": [], "fallback": {}, "projected": {}}
        assert _mint(conversion=[entry]) == _mint(conversion=record)

    def test_trace_only_members_never_move_the_cv(self):
        changed = {**_TRACE_ENTRY, "members": [], "projected": {"count": 9}}
        assert _mint(conversion=[changed]) == _mint(conversion=[_TRACE_ENTRY])

    def test_a_preimage_member_in_the_entry_moves_the_cv(self):
        changed = {**_TRACE_ENTRY, "rate_table_version": "rt-2"}
        assert _mint(conversion=[changed]) != _mint(conversion=[_TRACE_ENTRY])

    @pytest.mark.parametrize(
        "entries", [[], [_TRACE_ENTRY, _TRACE_ENTRY]], ids=["empty", "two-entries"]
    )
    def test_an_array_of_any_other_length_refuses(self, entries):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion=entries)
        assert excinfo.value.category == "schema_violation"

    def test_a_non_object_entry_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion=["fx_convert"])
        assert excinfo.value.category == "schema_violation"

    def test_a_string_conversion_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion="fx_convert")
        assert excinfo.value.category == "schema_violation"

    def test_an_entry_member_outside_the_closed_set_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion=[{**_TRACE_ENTRY, "native_facets": {}}])
        assert excinfo.value.category == "unknown_member"

    def test_trace_only_members_refuse_on_the_record_form(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion={**_RECORD, "members": []})
        assert excinfo.value.category == "unknown_member"

    def test_an_entry_missing_rate_table_version_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(conversion=[{"recipe": "fx_convert", "members": []}])
        assert excinfo.value.category == "schema_violation"


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
    def _fragment(self, **overrides: object) -> FrameTrace:
        kwargs: dict[str, object] = {
            "graph_revision": "graph-rev-1",
            "source_revisions": tuple(_SOURCE_REVISIONS),
            "facets": tuple(_FACETS),
            "frame_grammar_version": "frame1.0.0",
        }
        kwargs.update(overrides)
        return FrameTrace(**kwargs)

    def test_recompute_after_persisting_bytes_and_fragment_matches_the_minted_cv(self):
        canonical_bytes = b"the exact stored canonical frame bytes"
        frame_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
        minted = mint_content_version(
            frame_sha256=frame_sha256,
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
        )
        fragment = self._fragment()
        assert recompute_content_version(canonical_bytes, fragment) == minted

    def test_recompute_with_a_conversion_record_matches_the_minted_cv(self):
        canonical_bytes = b"other stored canonical frame bytes"
        frame_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
        conversion = {"recipe": "fx_convert", "rate_table_version": "rt-1"}
        minted = mint_content_version(
            frame_sha256=frame_sha256,
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
            conversion=conversion,
        )
        fragment = self._fragment(conversion=conversion)
        assert recompute_content_version(canonical_bytes, fragment) == minted

    def test_recompute_with_a_trace_conversion_array_matches_the_minted_cv(self):
        canonical_bytes = b"converted stored canonical frame bytes"
        minted = mint_content_version(
            frame_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
            conversion=_RECORD,
        )
        fragment = self._fragment(conversion=(_TRACE_ENTRY,))
        assert recompute_content_version(canonical_bytes, fragment) == minted

    def test_mutating_one_stored_byte_is_detected_as_a_mismatch(self):
        canonical_bytes = bytearray(b"the exact stored canonical frame bytes")
        frame_sha256 = hashlib.sha256(bytes(canonical_bytes)).hexdigest()
        minted = mint_content_version(
            frame_sha256=frame_sha256,
            graph_revision="graph-rev-1",
            source_revisions=_SOURCE_REVISIONS,
            facets=_FACETS,
        )
        fragment = self._fragment()

        mutated = bytearray(canonical_bytes)
        mutated[0] ^= 0xFF
        assert recompute_content_version(bytes(mutated), fragment) != minted


class TestCollectionContentVersion:
    def test_shuffled_member_input_order_mints_the_same_cv(self):
        members = [
            (_SPEC_A, _MEMBER_CV_1),
            (_SPEC_B, _MEMBER_CV_2),
            (_SPEC_C, _MEMBER_CV_3),
        ]
        shuffled = [members[2], members[0], members[1]]
        assert members != shuffled
        assert mint_collection_content_version(
            members
        ) == mint_collection_content_version(shuffled)

    def test_perturbing_one_members_cv_moves_the_collection_cv(self):
        base = [(_SPEC_A, _MEMBER_CV_1), (_SPEC_B, _MEMBER_CV_2)]
        perturbed = [(_SPEC_A, _MEMBER_CV_1), (_SPEC_B, _MEMBER_CV_9)]
        assert mint_collection_content_version(base) != mint_collection_content_version(
            perturbed
        )

    def test_adding_a_member_moves_the_collection_cv(self):
        base = [(_SPEC_A, _MEMBER_CV_1)]
        extended = [(_SPEC_A, _MEMBER_CV_1), (_SPEC_B, _MEMBER_CV_2)]
        assert mint_collection_content_version(base) != mint_collection_content_version(
            extended
        )

    def test_malformed_member_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            mint_collection_content_version([(_SPEC_A,)])
        assert excinfo.value.category == "schema_violation"

    def test_non_iterable_member_refuses_without_raising_a_type_error(self):
        # tuple(42) raises a raw TypeError; that must be caught and
        # re-raised as this module's own refusal category, never surfaced
        # as an implementation-level type error.
        with pytest.raises(ContentVersionError) as excinfo:
            mint_collection_content_version([42])
        assert excinfo.value.category == "schema_violation"

    def test_frame_level_and_collection_level_preimages_do_not_collide(self):
        # Distinct domain separators ("cv1" vs "colcv1") on structurally
        # different documents -- proof by exhaustion over a small battery
        # rather than an appeal to the separator alone.
        frame_level = {
            _mint(),
            _mint(conversion={"recipe": "fx_convert", "rate_table_version": "rt-1"}),
        }
        collection_level = {
            mint_collection_content_version([(_SPEC_A, _MEMBER_CV_1)]),
            mint_collection_content_version(
                [(_SPEC_A, _MEMBER_CV_1), (_SPEC_B, _MEMBER_CV_2)]
            ),
        }
        assert frame_level.isdisjoint(collection_level)

    def test_duplicate_spec_id_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            mint_collection_content_version(
                [(_SPEC_A, _MEMBER_CV_1), (_SPEC_A, _MEMBER_CV_2)]
            )
        assert excinfo.value.category == "duplicate_spec_id"

    def test_duplicate_spec_id_refuses_regardless_of_which_order_the_pair_arrives_in(
        self,
    ):
        # The order-reversal case: a stable sort alone would silently order
        # these two differently-cv'd duplicates one way or the other,
        # minting two different tokens for "the same contradictory input" --
        # refusing before the sort is what keeps this genuinely
        # order-independent rather than order-independent by accident.
        forward = [(_SPEC_A, _MEMBER_CV_1), (_SPEC_A, _MEMBER_CV_2)]
        reversed_order = list(reversed(forward))
        for members in (forward, reversed_order):
            with pytest.raises(ContentVersionError) as excinfo:
                mint_collection_content_version(members)
            assert excinfo.value.category == "duplicate_spec_id"

    @pytest.mark.parametrize(
        "member",
        [
            ("", _MEMBER_CV_1),
            ("spec_" + "a" * 32, ""),
            ("SPEC_" + "a" * 32, _MEMBER_CV_1),
            ("spec_" + "a" * 32, "CV_" + "1" * 32),
            ("spec_" + "g" * 32, _MEMBER_CV_1),
            ("spec_" + "a" * 32, "cv_" + "g" * 32),
            ("spec_" + "a" * 31, _MEMBER_CV_1),
            ("spec_" + "a" * 33, _MEMBER_CV_1),
            (_SPEC_A, "cv_" + "1" * 31),
            (_SPEC_A, "cv_" + "1" * 33),
        ],
    )
    def test_malformed_member_token_refuses(self, member):
        with pytest.raises(ContentVersionError) as excinfo:
            mint_collection_content_version([member])
        assert excinfo.value.category in {
            "invalid_spec_id",
            "invalid_member_content_version",
            "schema_violation",
        }


class TestUnicodeNormalisation:
    """NFC convergence for every `content_version` field that runs through
    `_nfc_string`, mirroring `test_frame.py`'s own
    `TestUnicodeNormalisation` for the frame grammar's own fields.

    `facets[].axis` is not covered here: the closed axis vocabulary
    (`measurementMethod`, `provenance`, `unit`) has no accented characters,
    so no byte-distinct spelling of a valid axis name can converge under
    NFC to a different valid axis name -- there is no convergence case to
    construct for this field, only the surrogate-refusal case below.
    """

    _DECOMPOSED = "café"  # "café" spelled with a combining acute accent
    _PRECOMPOSED = "café"  # the same word, NFC-composed

    def test_graph_revision_converges(self):
        assert self._DECOMPOSED != self._PRECOMPOSED
        assert _mint(graph_revision=self._DECOMPOSED) == _mint(
            graph_revision=self._PRECOMPOSED
        )

    def test_source_revisions_source_converges(self):
        a = [{"source": self._DECOMPOSED, "revision": "rev-1"}]
        b = [{"source": self._PRECOMPOSED, "revision": "rev-1"}]
        assert _mint(source_revisions=a) == _mint(source_revisions=b)

    def test_source_revisions_revision_converges(self):
        a = [{"source": "S", "revision": self._DECOMPOSED}]
        b = [{"source": "S", "revision": self._PRECOMPOSED}]
        assert _mint(source_revisions=a) == _mint(source_revisions=b)

    def test_conversion_recipe_converges(self):
        a = _mint(conversion={"recipe": self._DECOMPOSED, "rate_table_version": "rt-1"})
        b = _mint(
            conversion={"recipe": self._PRECOMPOSED, "rate_table_version": "rt-1"}
        )
        assert a == b

    def test_conversion_rate_table_version_converges(self):
        a = _mint(conversion={"recipe": "r", "rate_table_version": self._DECOMPOSED})
        b = _mint(conversion={"recipe": "r", "rate_table_version": self._PRECOMPOSED})
        assert a == b

    def test_conversion_deflator_table_version_converges(self):
        a = _mint(
            conversion={
                "recipe": "r",
                "rate_table_version": "rt-1",
                "deflator_table_version": self._DECOMPOSED,
            }
        )
        b = _mint(
            conversion={
                "recipe": "r",
                "rate_table_version": "rt-1",
                "deflator_table_version": self._PRECOMPOSED,
            }
        )
        assert a == b


class TestLoneSurrogateRefusal:
    """Every `content_version` field that runs through `_nfc_string` refuses
    an unpaired UTF-16 surrogate, mirroring
    `test_frame.py`'s own `TestLoneSurrogateRefusal`."""

    _LONE_SURROGATE = "\ud800"

    def test_graph_revision_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(graph_revision=self._LONE_SURROGATE)
        assert excinfo.value.category == "lone_surrogate"

    def test_source_revisions_source_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(source_revisions=[{"source": self._LONE_SURROGATE, "revision": "r"}])
        assert excinfo.value.category == "lone_surrogate"

    def test_source_revisions_revision_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(source_revisions=[{"source": "S", "revision": self._LONE_SURROGATE}])
        assert excinfo.value.category == "lone_surrogate"

    def test_facets_axis_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(facets=[{"axis": self._LONE_SURROGATE, "ref": "x", "basis": "bound"}])
        assert excinfo.value.category == "lone_surrogate"

    def test_conversion_recipe_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                conversion={
                    "recipe": self._LONE_SURROGATE,
                    "rate_table_version": "rt-1",
                }
            )
        assert excinfo.value.category == "lone_surrogate"

    def test_conversion_rate_table_version_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                conversion={"recipe": "r", "rate_table_version": self._LONE_SURROGATE}
            )
        assert excinfo.value.category == "lone_surrogate"

    def test_conversion_deflator_table_version_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                conversion={
                    "recipe": "r",
                    "rate_table_version": "rt-1",
                    "deflator_table_version": self._LONE_SURROGATE,
                }
            )
        assert excinfo.value.category == "lone_surrogate"

    def test_collection_member_spec_id_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            mint_collection_content_version([(self._LONE_SURROGATE, _MEMBER_CV_1)])
        assert excinfo.value.category == "lone_surrogate"

    def test_collection_member_content_version_refuses(self):
        with pytest.raises(ContentVersionError) as excinfo:
            mint_collection_content_version([(_SPEC_A, self._LONE_SURROGATE)])
        assert excinfo.value.category == "lone_surrogate"


class TestUnitRequestBaseYearType:
    """`base_year` must be a genuine `int`, not a `bool` (a `bool` is an
    `int` subclass in Python), a `float`, or a numeral spelled as a
    string."""

    @pytest.mark.parametrize("base_year", [True, 2023.0, "2023"])
    def test_non_int_base_year_refuses(self, base_year):
        with pytest.raises(ContentVersionError) as excinfo:
            _mint(
                facets=[
                    {
                        "axis": "unit",
                        "ref": {
                            "currency": "USD",
                            "prices": "constant",
                            "base_year": base_year,
                        },
                        "basis": "bound",
                    }
                ]
            )
        assert excinfo.value.category == "invalid_unit"


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
content_version = mint_content_version(
    frame_sha256=digest,
    graph_revision="graph-rev-1",
    source_revisions=[
        {"source": "OECD_DAC_CRS", "revision": "rev-1"},
        {"source": "WORLD_BANK_WDI", "revision": "rev-2"},
    ],
    facets=[
        {"axis": "provenance", "ref": "P1", "basis": "bound"},
        {"axis": "measurementMethod", "ref": "M1", "basis": "bound"},
    ],
)
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
