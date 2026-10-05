"""`Frame.build` and `canonical_frame_bytes` -- the pinned frame-bytes rules
from `docs/frame-grammar-v1.md`: column order, cell domains, NFC
normalisation, lone-surrogate refusal, int/float value convergence,
time-period validation, row-key uniqueness, and the row sort.

Every token here is proved by perturbation: a rule is exercised by changing
one input and observing whether the canonical bytes move, never by
asserting a docstring's claim in isolation.
"""

from __future__ import annotations

import dataclasses
import json
import random

import pytest
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar.frame import Frame, canonical_frame_bytes
from dfo_frame_grammar.grammar import FrameError


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/FRA",
        "recipient": "country/TGO",
        "time_period": "2015",
        "value": 1000.0,
    }
    row.update(overrides)
    return row


_ROLE_KEYS = ("provider", "recipient")


class TestEmptyFrame:
    def test_builds_with_zero_rows(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[])
        assert frame.rows == ()
        assert frame.columns[0] == "stat_var"
        assert frame.columns[-1] == "status"

    def test_mints_and_differs_from_a_non_empty_frame(self):
        empty = Frame.build(role_keys=_ROLE_KEYS, rows=[])
        non_empty = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        empty_bytes = canonical_frame_bytes(empty)
        non_empty_bytes = canonical_frame_bytes(non_empty)
        assert empty_bytes != non_empty_bytes
        document = json.loads(empty_bytes)
        assert document["rows"] == []
        assert document["frame_version"] == "frame1"

    def test_uses_the_complete_canonical_arrow_schema(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[])
        assert frame.table.schema.names == list(frame.columns)
        assert str(frame.table.schema.field("unit").type).startswith("struct<ref:")

    def test_canonical_bytes_do_not_access_the_compatibility_rows_view(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])

        def fail(_self: Frame) -> tuple[tuple[object, ...], ...]:
            raise AssertionError("canonical serialisation accessed Frame.rows")

        monkeypatch.setattr(Frame, "rows", property(fail))
        assert json.loads(canonical_frame_bytes(frame))["rows"]


class TestFrameEquality:
    def test_columns_are_part_of_frame_identity(self) -> None:
        built = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        original = Frame(columns=built.columns, table=built.table)
        same = Frame(columns=built.columns, table=built.table)
        different_columns = Frame(
            columns=(built.columns[1], built.columns[0], *built.columns[2:]),
            table=built.table,
        )

        assert original == same
        assert hash(original) == hash(same)
        assert original != different_columns


class TestColumnOrderAndCellShape:
    def test_document_shape_matches_the_pinned_grammar(self):
        frame = Frame.build(
            role_keys=_ROLE_KEYS,
            rows=[
                _row(
                    unit="USD",
                    measurementMethod="GrantEquivalent",
                    provenance="OECD_DAC2A",
                )
            ],
        )
        document = json.loads(canonical_frame_bytes(frame))
        assert document["columns"] == [
            "stat_var",
            "provider",
            "recipient",
            "time_period",
            "value",
            "unit",
            "measurementMethod",
            "provenance",
            "status",
        ]
        assert document["rows"] == [
            [
                "dcid:MalariaGrantDisbursements",
                "country/FRA",
                "country/TGO",
                "2015",
                1000.0,
                "USD",
                "GrantEquivalent",
                "OECD_DAC2A",
                None,
            ]
        ]

    def test_facet_and_unit_cells_default_to_null_when_unbound(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        row = frame.rows[0]
        cell = dict(zip(frame.columns, row, strict=True))
        assert cell["unit"] is None
        assert cell["measurementMethod"] is None
        assert cell["provenance"] is None

    def test_status_is_always_null(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        cell = dict(zip(frame.columns, frame.rows[0], strict=True))
        assert cell["status"] is None

    def test_unit_request_object_round_trips(self):
        unit = {"currency": "EUR", "prices": "constant", "base_year": 2023}
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(unit=unit)])
        cell = dict(zip(frame.columns, frame.rows[0], strict=True))
        # Stored as `grammar.UnitRequest`, not the plain mapping supplied
        # (see `TestUnitRequestCellIsImmutable` below); `as_json` is its
        # round trip back to the plain object.
        assert cell["unit"].as_json() == unit


class TestValueDomain:
    def test_int_and_float_spellings_converge_to_identical_bytes(self):
        frame_int = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=1000)])
        frame_float = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=1000.0)])
        assert canonical_frame_bytes(frame_int) == canonical_frame_bytes(frame_float)

    def test_changing_the_value_moves_the_bytes(self):
        frame_a = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=1.0)])
        frame_b = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=2.0)])
        assert canonical_frame_bytes(frame_a) != canonical_frame_bytes(frame_b)

    @pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_value_refuses(self, bad_value):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=bad_value)])
        assert excinfo.value.category == "non_finite_number"

    def test_boolean_value_refuses_as_wrong_type(self):
        # bool is a subclass of int in Python; must never pass as a number.
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=True)])
        assert excinfo.value.category == "schema_violation"

    def test_string_value_refuses_as_wrong_type(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value="1000")])
        assert excinfo.value.category == "schema_violation"

    def test_integer_too_large_for_a_finite_double_refuses_without_an_overflow_error(
        self,
    ):
        # `float()` on an int this large raises OverflowError; that must be
        # caught and re-raised as this package's own refusal category, never
        # surfaced as a raw OverflowError.
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=10**400)])
        assert excinfo.value.category == "non_finite_number"


class TestTimePeriodDomain:
    def test_malformed_time_period_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="15")])
        assert excinfo.value.category == "invalid_time_period"


class TestUnitDomain:
    def test_invalid_unit_request_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(
                role_keys=_ROLE_KEYS,
                rows=[_row(unit={"currency": "USD", "prices": "constant"})],
            )
        assert excinfo.value.category == "invalid_unit"

    def test_unit_of_wrong_type_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(unit=42)])
        assert excinfo.value.category == "schema_violation"


class TestValueBoundaryMagnitudes:
    """Boundary-magnitude values at the exponent-notation thresholds the
    document names. These are delegated to `rfc8785`'s own ECMAScript
    number lexical form -- this only proves the value survives
    construction and round-trips through the frame unchanged, not that this
    package reimplements the lexical rule itself."""

    @pytest.mark.parametrize("value", [1e21, 1e-7, 5e-324, 1.79e308])
    def test_boundary_value_builds_and_round_trips(self, value):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=value)])
        document = json.loads(canonical_frame_bytes(frame))
        value_index = frame.columns.index("value")
        assert document["rows"][0][value_index] == value


class TestUnicodeNormalisation:
    _DECOMPOSED = "café"  # "café" spelled with a combining acute accent
    _PRECOMPOSED = "café"  # the same word, NFC-composed

    def test_decomposed_and_precomposed_refs_produce_identical_bytes(self):
        assert self._DECOMPOSED != self._PRECOMPOSED

        frame_decomposed = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(provenance=self._DECOMPOSED)]
        )
        frame_precomposed = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(provenance=self._PRECOMPOSED)]
        )
        assert canonical_frame_bytes(frame_decomposed) == canonical_frame_bytes(
            frame_precomposed
        )

    def test_role_key_collision_after_nfc_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(
                role_keys=[self._DECOMPOSED, self._PRECOMPOSED],
                rows=[],
            )
        assert excinfo.value.category == "schema_violation"

    def test_two_rows_colliding_into_the_same_grain_only_after_nfc_refuse(self):
        # Byte-distinct as typed, but the same grain once both are
        # NFC-normalised -- a different code path from the role-key
        # collision above, which fires during column derivation rather
        # than row-key comparison.
        row_a = _row(provenance=self._DECOMPOSED)
        row_b = _row(provenance=self._PRECOMPOSED)
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[row_a, row_b])
        assert excinfo.value.category == "duplicate_row_key"

    def test_decomposed_role_key_and_row_key_converge_with_the_precomposed_spelling(
        self,
    ):
        # Mirrors the frame-multi-role-unicode-order /
        # frame-row-key-decomposed-spelling-converges vector pair: both
        # role_keys and the row mapping's own keys are spelled decomposed,
        # converging on identical bytes to an otherwise identical frame
        # spelled precomposed throughout.
        decomposed_role = "état"  # "état" with a combining acute accent
        precomposed_role = "état"
        assert decomposed_role != precomposed_role

        frame_decomposed = Frame.build(
            role_keys=["provider", "recipient", decomposed_role],
            rows=[_row(**{decomposed_role: "country/FRA"})],
        )
        frame_precomposed = Frame.build(
            role_keys=["provider", "recipient", precomposed_role],
            rows=[_row(**{precomposed_role: "country/FRA"})],
        )
        assert canonical_frame_bytes(frame_decomposed) == canonical_frame_bytes(
            frame_precomposed
        )

    def test_row_carrying_two_distinct_raw_keys_colliding_after_nfc_refuses(self):
        # Byte-distinct as typed, but the same key once both are
        # NFC-normalised -- constructed as a dict literal so both keys
        # survive as distinct dict entries going into Frame.build, the same
        # way the declared-role-key collision above is constructed from two
        # distinct list entries rather than one.
        row = _row()
        row[self._PRECOMPOSED] = "a"
        row[self._DECOMPOSED] = "b"
        assert len(row) == 7  # both keys survived as distinct dict entries
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[row])
        assert excinfo.value.category == "schema_violation"

    def test_non_string_row_key_refuses(self):
        row = _row()
        row[1] = "x"  # type: ignore[index]
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[row])
        assert excinfo.value.category == "schema_violation"


class TestRoleKeysAcceptAGenerator:
    """`role_keys` is typed `Iterable[str]`; a caller passing a one-shot
    generator must not have it silently exhausted after the first internal
    pass, which would drop every role column from the resulting frame."""

    def test_generator_role_keys_produce_identical_bytes_to_the_tuple_form(self):
        role_dicts = [{"property": "provider"}, {"property": "recipient"}]
        frame_from_generator = Frame.build(
            role_keys=(entry["property"] for entry in role_dicts),
            rows=[_row()],
        )
        frame_from_tuple = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        assert frame_from_generator.columns == frame_from_tuple.columns
        assert canonical_frame_bytes(frame_from_generator) == canonical_frame_bytes(
            frame_from_tuple
        )
        assert "provider" in frame_from_generator.columns
        assert "recipient" in frame_from_generator.columns


class TestRoleKeyReservedColumnNameCollision:
    """A declared role name equal to a reserved column name would derive a
    `columns` array carrying that name twice; refused rather than silently
    producing a positionally-ambiguous document."""

    @pytest.mark.parametrize(
        "reserved",
        [
            "stat_var",
            "time_period",
            "value",
            "unit",
            "measurementMethod",
            "provenance",
            "status",
        ],
    )
    def test_role_key_equal_to_a_reserved_column_name_refuses(self, reserved):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=[reserved, "provider"], rows=[])
        assert excinfo.value.category == "schema_violation"


class TestLoneSurrogateRefusal:
    _LONE_SURROGATE = "\ud800"

    def test_stat_var_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(
                role_keys=_ROLE_KEYS, rows=[_row(stat_var=self._LONE_SURROGATE)]
            )
        assert excinfo.value.category == "lone_surrogate"

    def test_role_cell_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(
                role_keys=_ROLE_KEYS, rows=[_row(provider=self._LONE_SURROGATE)]
            )
        assert excinfo.value.category == "lone_surrogate"

    def test_unit_string_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(unit=self._LONE_SURROGATE)])
        assert excinfo.value.category == "lone_surrogate"


class TestNonEmptyStringDomain:
    """`stat_var`, role columns, the bare-string form of `unit`, and
    facet-identity columns are all documented as non-empty strings; an
    upstream bug passing `""` where it meant `None` or a real ref must
    refuse rather than mint a distinct, permanent identity-bearing value."""

    def test_empty_stat_var_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(stat_var="")])
        assert excinfo.value.category == "schema_violation"

    def test_empty_role_cell_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(provider="")])
        assert excinfo.value.category == "schema_violation"

    def test_empty_bare_string_unit_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(unit="")])
        assert excinfo.value.category == "schema_violation"

    @pytest.mark.parametrize("axis", ["measurementMethod", "provenance"])
    def test_empty_facet_identity_column_refuses(self, axis):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(**{axis: ""})])
        assert excinfo.value.category == "schema_violation"


class TestStatVarCanonicalSpelling:
    """`stat_var` must carry the grammar's one canonical spelling -- a
    `dcid:`-prefixed reference -- so two spellings of the same StatVar
    cannot mint two different sets of canonical bytes."""

    def test_unprefixed_dcid_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(
                role_keys=_ROLE_KEYS, rows=[_row(stat_var="MalariaGrantDisbursements")]
            )
        assert excinfo.value.category == "schema_violation"

    def test_display_label_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(stat_var="Malaria Grants")])
        assert excinfo.value.category == "schema_violation"

    def test_prefixed_form_still_converges_as_before(self):
        frame_a = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(stat_var="dcid:MalariaGrantDisbursements")]
        )
        frame_b = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(stat_var="dcid:MalariaGrantDisbursements")]
        )
        assert canonical_frame_bytes(frame_a) == canonical_frame_bytes(frame_b)


class TestUnitRequestCellIsImmutable:
    """A unit-request cell is stored as `grammar.UnitRequest`, not a plain
    mapping: mutating it after `Frame.build` would change what the same
    `Frame` object canonicalises to, and could make two rows that were
    distinct at construction time collide into the same grain after the
    row-uniqueness check already ran."""

    def test_post_build_mutation_is_impossible(self):
        unit = {"currency": "EUR", "prices": "current"}
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(unit=unit)])
        unit_index = frame.columns.index("unit")
        cell = frame.rows[0][unit_index]
        assert isinstance(cell, grammar.UnitRequest)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cell.currency = "USD"  # type: ignore[misc]

    def test_mutation_cannot_bypass_row_uniqueness(self):
        # Two rows, distinct only by their unit-request currency -- a
        # genuinely different grain, so both are accepted.
        row_a = _row(provenance="A", unit={"currency": "EUR", "prices": "current"})
        row_b = _row(provenance="A", unit={"currency": "USD", "prices": "current"})
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[row_a, row_b])
        assert len(frame.rows) == 2

        # If a caller could mutate one row's unit-request cell to match the
        # other's after the fact, the two rows would then share a grain the
        # uniqueness check already passed on -- impossible here, since the
        # cell has no attribute a caller can assign to.
        unit_index = frame.columns.index("unit")
        with pytest.raises(dataclasses.FrozenInstanceError):
            frame.rows[0][unit_index].currency = "USD"  # type: ignore[misc]


class TestRowShapeValidation:
    def test_missing_required_column_refuses(self):
        bad_row = _row()
        del bad_row["time_period"]
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[bad_row])
        assert excinfo.value.category == "schema_violation"

    def test_unknown_column_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(unexpected="x")])
        assert excinfo.value.category == "schema_violation"

    def test_caller_supplied_status_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[_row(status="provisional")])
        assert excinfo.value.category == "schema_violation"


class TestDuplicateRowKeyRefusal:
    def test_two_rows_sharing_a_grain_refuse_even_with_different_values(self):
        row_a = _row(value=1.0)
        row_b = _row(value=2.0)  # identical grain, different value
        with pytest.raises(FrameError) as excinfo:
            Frame.build(role_keys=_ROLE_KEYS, rows=[row_a, row_b])
        assert excinfo.value.category == "duplicate_row_key"

    def test_rows_differing_only_by_time_period_are_not_duplicates(self):
        frame = Frame.build(
            role_keys=_ROLE_KEYS,
            rows=[_row(time_period="2015"), _row(time_period="2016")],
        )
        assert len(frame.rows) == 2


class TestRowOrderInvariance:
    def test_shuffled_input_row_order_produces_identical_bytes(self):
        rows = [
            _row(time_period=f"20{year:02d}", value=float(year))
            for year in range(10, 20)
        ]
        shuffled = list(rows)
        rng = random.Random(1234)
        rng.shuffle(shuffled)
        assert shuffled != rows  # the shuffle actually changed the order

        frame_in_order = Frame.build(role_keys=_ROLE_KEYS, rows=rows)
        frame_shuffled = Frame.build(role_keys=_ROLE_KEYS, rows=shuffled)
        assert canonical_frame_bytes(frame_in_order) == canonical_frame_bytes(
            frame_shuffled
        )

    def test_rows_are_stored_sorted_regardless_of_input_order(self):
        rows = [_row(time_period="2016"), _row(time_period="2015")]
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=rows)
        time_period_index = frame.columns.index("time_period")
        assert [row[time_period_index] for row in frame.rows] == ["2015", "2016"]

    def test_frame_build_sorts_one_column_mixing_none_strings_and_a_unit_request(self):
        # `TestCellSortKey`/`TestRowSortKey` exercise the sort functions
        # directly with hand-built tuples; this proves `Frame.build` itself
        # wires the same collation into a real column (`unit`) carrying all
        # three cell types the grammar allows there, across several rows.
        rows = [
            _row(time_period="2017", unit={"currency": "USD", "prices": "current"}),
            _row(time_period="2015", unit=None),
            _row(time_period="2016", unit="EUR"),
        ]
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=rows)
        unit_index = frame.columns.index("unit")
        ordered_units = [row[unit_index] for row in frame.rows]
        assert ordered_units[0] is None
        assert ordered_units[1] == "EUR"
        assert isinstance(ordered_units[2], grammar.UnitRequest)
        assert ordered_units[2].as_json() == {"currency": "USD", "prices": "current"}


class TestCanonicalFrameBytesIsPureJcs:
    def test_no_local_number_formatting_artifacts_survive(self):
        # -0.0 collapses to 0, matching JCS/ES6 number serialisation --
        # proof that no local formatting path re-introduces the sign.
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=-0.0)])
        document = json.loads(canonical_frame_bytes(frame))
        assert document["rows"][0][4] == 0
        assert "-0" not in canonical_frame_bytes(frame).decode("utf-8")


class TestReservedStatusColumnMovesTheDigest:
    """The reserved `status` column is inside the canonical bytes from
    launch, so dropping it, or populating it, changes what a frame hashes
    to -- and a hypothetical later-minor grammar that
    starts allowing `status` to carry a real value produces byte-identical
    output to today's grammar for any frame where every row's status stays
    `null`. `Frame.build` has no way to construct a document missing
    `status` or carrying a non-null one (it is reserved and always `None`),
    so these simulate the comparison directly against the same JCS
    primitive `canonical_frame_bytes` itself calls.
    """

    def test_dropping_status_from_columns_moves_the_digest(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        real_bytes = canonical_frame_bytes(frame)

        status_index = frame.columns.index("status")
        columns_without_status = [c for c in frame.columns if c != "status"]
        rows_without_status = [
            [cell for i, cell in enumerate(row) if i != status_index]
            for row in frame.rows
        ]
        simulated = canonicalize(
            {
                "frame_version": "frame1",
                "columns": columns_without_status,
                "rows": rows_without_status,
            }
        )
        assert simulated != real_bytes

    def test_a_simulated_later_minor_populating_zero_statuses_is_byte_identical(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        real_bytes = canonical_frame_bytes(frame)

        # A later-minor grammar that merely starts *allowing* status to
        # carry a qualification produces this identical document for any
        # frame where every row's status is still null -- there is nothing
        # for the additive change to touch on such a frame.
        simulated_future = canonicalize(
            {
                "frame_version": "frame1",
                "columns": list(frame.columns),
                "rows": [list(row) for row in frame.rows],
            }
        )
        assert simulated_future == real_bytes

    def test_populating_one_rows_status_moves_the_digest(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        real_bytes = canonical_frame_bytes(frame)

        status_index = frame.columns.index("status")
        mutated_row = list(frame.rows[0])
        mutated_row[status_index] = "provisional"
        simulated_future = canonicalize(
            {
                "frame_version": "frame1",
                "columns": list(frame.columns),
                "rows": [mutated_row],
            }
        )
        assert simulated_future != real_bytes


class TestColumnReorderOrRenameMovesTheDigest:
    def test_reordering_two_columns_moves_the_digest(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        real_bytes = canonical_frame_bytes(frame)

        columns = list(frame.columns)
        i, j = columns.index("time_period"), columns.index("value")
        columns[i], columns[j] = columns[j], columns[i]
        rows = []
        for row in frame.rows:
            mutated = list(row)
            mutated[i], mutated[j] = mutated[j], mutated[i]
            rows.append(mutated)

        reordered = canonicalize(
            {"frame_version": "frame1", "columns": columns, "rows": rows}
        )
        assert reordered != real_bytes

    def test_renaming_one_column_moves_the_digest(self):
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        real_bytes = canonical_frame_bytes(frame)

        columns = [c if c != "unit" else "unit_renamed" for c in frame.columns]
        renamed = canonicalize(
            {
                "frame_version": "frame1",
                "columns": columns,
                "rows": [list(row) for row in frame.rows],
            }
        )
        assert renamed != real_bytes


class TestFacetRefIsIndependentOfAnyDispatchFacetId:
    """The frame grammar pins that a facet cell carries only the resolved
    canonical ref, never a store-side dispatch facet id (see `grammar`'s
    module docstring). `Frame.build`'s row schema has no column for one, so
    the same canonical ref arriving under two different dispatch facet ids
    is, from `Frame.build`'s perspective, the identical input either way,
    and necessarily produces identical bytes.
    """

    def test_no_column_exists_to_carry_a_dispatch_facet_id(self):
        with pytest.raises(FrameError) as excinfo:
            Frame.build(
                role_keys=_ROLE_KEYS, rows=[_row(dispatch_facet_id="fid_12345")]
            )
        assert excinfo.value.category == "schema_violation"

    def test_same_canonical_ref_converges_regardless_of_which_dispatch_id_resolved_it(
        self,
    ):
        # Simulates two different dispatch facet ids that both resolve to
        # the same canonical ref: Frame.build only ever sees the ref, so
        # the two frames it builds from them are byte-identical.
        frame_from_dispatch_a = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(provenance="OECD_DAC2A")]
        )
        frame_from_dispatch_b = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(provenance="OECD_DAC2A")]
        )
        assert canonical_frame_bytes(frame_from_dispatch_a) == canonical_frame_bytes(
            frame_from_dispatch_b
        )
