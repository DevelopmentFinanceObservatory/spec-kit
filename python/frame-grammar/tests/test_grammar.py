"""Column derivation, cell-domain validators, and the row-sort collation
(`dfo_frame_grammar.grammar`) -- the pinned rules from `docs/frame-grammar-v1.md`'s
"The frame" section, exercised directly against the pure functions rather
than through a built `Frame`.
"""

from __future__ import annotations

import dataclasses

import pytest

import dfo_frame_grammar
from dfo_frame_grammar import grammar
from dfo_frame_grammar.grammar import ComposedUnit, FrameError, StatVarPart, UnitRequest

_PERSON = {"stat_var": "dcid:Count_Person"}
_EUR_2020 = {"currency": "EUR", "prices": "constant", "base_year": 2020}


class TestDeriveColumns:
    def test_pinned_order_with_two_roles(self):
        # "provider" and "recipient" already sort by code point in that order.
        assert grammar.derive_columns(["provider", "recipient"]) == (
            "stat_var",
            "provider",
            "recipient",
            "time_period",
            "value",
            "unit",
            "measurementMethod",
            "provenance",
            "status",
        )

    def test_role_columns_sort_by_code_point_of_nfc_form_not_input_order(self):
        assert grammar.derive_columns(["recipient", "provider"]) == (
            "stat_var",
            "provider",
            "recipient",
            "time_period",
            "value",
            "unit",
            "measurementMethod",
            "provenance",
            "status",
        )

    def test_no_declared_roles(self):
        assert grammar.derive_columns([]) == (
            "stat_var",
            "time_period",
            "value",
            "unit",
            "measurementMethod",
            "provenance",
            "status",
        )

    def test_facet_identity_columns_are_measurement_method_then_provenance(self):
        assert grammar.FACET_IDENTITY_COLUMNS == ("measurementMethod", "provenance")

    def test_status_is_always_last(self):
        assert grammar.derive_columns(["a", "b", "c"])[-1] == "status"

    def test_duplicate_raw_role_key_is_not_a_collision(self):
        # The same raw key twice collapses to one column -- only two
        # DISTINCT raw keys colliding after NFC is a refusal.
        assert grammar.derive_columns(
            ["provider", "provider"]
        ) == grammar.derive_columns(["provider"])

    def test_distinct_role_keys_colliding_after_nfc_refuse(self):
        decomposed = "róle"  # "role" with a combining acute accent
        precomposed = "róle"  # NFC-composed spelling of the same string
        assert decomposed != precomposed
        with pytest.raises(FrameError) as excinfo:
            grammar.derive_columns([decomposed, precomposed])
        assert excinfo.value.category == "schema_violation"


class TestParseTimePeriod:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("2015", ("2015", "2015", "year")),
            ("2015-01", ("2015-01", "2015-01", "month")),
            ("2015-12", ("2015-12", "2015-12", "month")),
            ("2015-01-31", ("2015-01-31", "2015-01-31", "day")),
            ("2015-02-29", ("2015-02-29", "2015-02-29", "day")),
            ("2018/2022", ("2018", "2022", "year")),
            ("2018-01/2022-12", ("2018-01", "2022-12", "month")),
            ("2018-12-31/2019-01-01", ("2018-12-31", "2019-01-01", "day")),
        ],
    )
    def test_parses_points_and_same_grain_intervals(self, value, expected):
        start, end, grain = expected
        assert grammar.parse_time_period(value) == grammar.TimePeriod(
            start=start, end=end, grain=grain
        )
        grammar.validate_time_period(value)  # must not raise

    @pytest.mark.parametrize(
        "value",
        [
            "15",  # not zero-padded to 4 digits
            "2015-1",  # month not zero-padded
            "2015-13",  # month out of range
            "2015-01-32",  # day out of range
            "2015-01-01T00:00:00",  # not a bare date
            "",
            "2018/",  # open end
            "/2022",  # open start
            "2018/..",  # open end, ISO 8601-2 spelling
            "2018/P5Y",  # duration
            "P5Y/2022",  # duration
            "2018--2022",  # `--` separator
            "2018-01--2022-12",  # `--` separator
            "2018 / 2022",  # whitespace around the separator
            "2018/2022/2024",  # three points
            "2018/2022-12",  # mixed grain
            "2018-01-01/2022",  # mixed grain
            "2022/2018",  # reversed
            "2018-12/2018-01",  # reversed
            "2018/2018",  # equal ends: written as the point
            "2018-01-01/2018-01-01",  # equal ends: written as the point
        ],
    )
    def test_refuses_malformed_shapes(self, value):
        with pytest.raises(FrameError) as excinfo:
            grammar.parse_time_period(value)
        assert excinfo.value.category == "invalid_time_period"
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_time_period(value)
        assert excinfo.value.category == "invalid_time_period"

    @pytest.mark.parametrize(
        "value",
        [
            "٢٠٢٤",  # Arabic-Indic digits spelling the same year as "2024"
            "2024-01-2١",  # ASCII except its last digit  # noqa: RUF001
            "٢٠١٨/2022",  # an interval with a non-ASCII start
            "2018/202٢",  # an interval with a non-ASCII end
        ],
    )
    def test_refuses_non_ascii_decimal_digits(self, value):
        # `\d` matches any Unicode decimal digit without `re.ASCII`, and NFC
        # normalisation does not fold other scripts' digits to ASCII -- a
        # non-ASCII spelling must refuse rather than mint its own distinct
        # token for the same period.
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_time_period(value)
        assert excinfo.value.category == "invalid_time_period"


class TestValidateUnitRequest:
    def test_accepts_current_with_no_base_year(self):
        grammar.validate_unit_request({"currency": "USD", "prices": "current"})

    def test_accepts_constant_with_base_year(self):
        grammar.validate_unit_request(
            {"currency": "EUR", "prices": "constant", "base_year": 2023}
        )

    def test_refuses_unknown_member(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request(
                {"currency": "USD", "prices": "current", "extra": 1}
            )
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_constant_without_base_year(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request({"currency": "USD", "prices": "constant"})
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_current_with_base_year(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request(
                {"currency": "USD", "prices": "current", "base_year": 2023}
            )
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_lower_case_currency(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request({"currency": "usd", "prices": "current"})
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_invalid_prices_enum(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request({"currency": "USD", "prices": "nominal"})
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_prices_as_a_list_without_raising_a_type_error(self):
        # The isinstance check must run before the frozenset membership
        # test: an unhashable 'prices' would otherwise raise a raw
        # TypeError instead of this refusal.
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request({"currency": "USD", "prices": ["current"]})
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_base_year_at_the_jcs_safe_integer_boundary_plus_one(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request(
                {"currency": "USD", "prices": "constant", "base_year": 2**53}
            )
        assert excinfo.value.category == "invalid_unit"

    @pytest.mark.parametrize("base_year", [2**53 - 1, -(2**53 - 1)])
    def test_accepts_base_year_at_the_jcs_safe_integer_boundary(self, base_year):
        grammar.validate_unit_request(
            {"currency": "USD", "prices": "constant", "base_year": base_year}
        )  # must not raise

    def test_refuses_role_shaped_currency(self):
        # A frame's own per-row unit cell is always already resolved to one
        # concrete currency.
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request(
                {"currency": {"role": "origin"}, "prices": "current"}
            )
        assert excinfo.value.category == "invalid_unit"


class TestUnitCellFromJson:
    @pytest.mark.parametrize(
        ("value", "cell"),
        [
            (None, None),
            ("USDollar", "USDollar"),
            (_EUR_2020, UnitRequest("EUR", "constant", 2020)),
            (
                {"numerator": "USDollar", "denominator": _PERSON},
                ComposedUnit("USDollar", StatVarPart("dcid:Count_Person")),
            ),
            (
                {"numerator": _EUR_2020, "denominator": _PERSON},
                ComposedUnit(
                    UnitRequest("EUR", "constant", 2020),
                    StatVarPart("dcid:Count_Person"),
                ),
            ),
            (
                {"numerator": "USDollar", "denominator": "Euro"},
                ComposedUnit("USDollar", "Euro"),
            ),
            (
                {"numerator": _PERSON, "denominator": "Euro"},
                ComposedUnit(StatVarPart("dcid:Count_Person"), "Euro"),
            ),
        ],
    )
    def test_each_form_round_trips_through_json(self, value, cell):
        assert grammar.unit_cell_from_json(value) == cell
        assert grammar.unit_cell_to_json(cell) == value

    def test_part_strings_are_nfc_normalised(self):
        cell = grammar.unit_cell_from_json(
            {
                "numerator": "unit/e\u0301",
                "denominator": {"stat_var": "dcid:e\u0301"},
            }
        )
        assert cell == ComposedUnit("unit/\u00e9", StatVarPart("dcid:\u00e9"))

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(
                {
                    "numerator": "USDollar",
                    "denominator": {"numerator": "a", "denominator": "b"},
                },
                id="nested-composed-part",
            ),
            pytest.param(
                {"numerator": "USDollar", "denominator": _PERSON, "scale": 1000},
                id="unknown-member",
            ),
            pytest.param({"numerator": "USDollar"}, id="missing-denominator"),
            pytest.param({"denominator": "USDollar"}, id="missing-numerator"),
            pytest.param(
                {"numerator": "USDollar", "denominator": None}, id="null-part"
            ),
            pytest.param(
                {"numerator": "", "denominator": _PERSON}, id="empty-ref-part"
            ),
            pytest.param({"numerator": 42, "denominator": _PERSON}, id="number-part"),
            pytest.param(
                {"numerator": ["USDollar"], "denominator": _PERSON}, id="array-part"
            ),
            pytest.param(
                {"numerator": "USDollar", "denominator": {"stat_var": "Count_Person"}},
                id="stat-var-without-prefix",
            ),
            pytest.param(
                {"numerator": "USDollar", "denominator": {"stat_var": 7}},
                id="stat-var-not-a-string",
            ),
            pytest.param(
                {
                    "numerator": "USDollar",
                    "denominator": {"stat_var": "dcid:Count_Person", "label": "People"},
                },
                id="stat-var-part-extra-member",
            ),
            pytest.param(
                {
                    "numerator": {"currency": "EUR", "prices": "constant"},
                    "denominator": _PERSON,
                },
                id="invalid-unit-request-part",
            ),
            pytest.param(
                {
                    "numerator": {"currency": {"role": "origin"}, "prices": "current"},
                    "denominator": _PERSON,
                },
                id="role-currency-part",
            ),
            pytest.param({"numerator": "USDollar", "denominator": {}}, id="empty-part"),
            pytest.param({"stat_var": "dcid:Count_Person"}, id="bare-stat-var-cell"),
        ],
    )
    def test_refuses_a_value_outside_the_closed_shape(self, value):
        with pytest.raises(FrameError) as excinfo:
            grammar.unit_cell_from_json(value)
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_a_lone_surrogate_in_a_part(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.unit_cell_from_json({"numerator": "\ud800", "denominator": _PERSON})
        assert excinfo.value.category == "lone_surrogate"


class TestComposedUnit:
    def test_equal_values_are_equal_and_hash_equal(self):
        a = ComposedUnit("USDollar", StatVarPart("dcid:Count_Person"))
        b = ComposedUnit("USDollar", StatVarPart("dcid:Count_Person"))
        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1

    def test_swapped_sides_and_different_parts_are_unequal(self):
        cell = ComposedUnit("USDollar", "Euro")
        assert cell != ComposedUnit("Euro", "USDollar")
        assert cell != ComposedUnit("USDollar", StatVarPart("dcid:Euro"))

    def test_is_immutable(self):
        cell = ComposedUnit("USDollar", StatVarPart("dcid:Count_Person"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            cell.numerator = "Euro"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            cell.denominator.stat_var = "dcid:X"  # type: ignore[misc]

    def test_as_json_builds_a_fresh_object(self):
        cell = ComposedUnit(UnitRequest("EUR", "constant", 2020), "Euro")
        first = cell.as_json()
        first["numerator"]["currency"] = "USD"  # type: ignore[index]
        assert cell.as_json() == {"numerator": _EUR_2020, "denominator": "Euro"}


def test_package_root_exports_the_unit_cell_surface():
    for name in (
        "ComposedUnit",
        "StatVarPart",
        "UnitCell",
        "UnitPart",
        "UnitRequest",
        "unit_cell_array",
        "unit_cell_from_json",
        "unit_cell_to_json",
    ):
        assert name in dfo_frame_grammar.__all__
        assert hasattr(dfo_frame_grammar, name)
    assert dfo_frame_grammar.unit_cell_from_json is grammar.unit_cell_from_json


class TestRejectLoneSurrogates:
    def test_ordinary_string_passes(self):
        grammar.reject_lone_surrogates("café")  # must not raise

    def test_lone_surrogate_refuses(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.reject_lone_surrogates("\ud800")
        assert excinfo.value.category == "lone_surrogate"


class TestCellSortKey:
    def test_none_sorts_before_any_string(self):
        assert grammar.cell_sort_key(None) < grammar.cell_sort_key("")
        assert grammar.cell_sort_key(None) < grammar.cell_sort_key("a")

    def test_strings_compare_by_code_point(self):
        assert grammar.cell_sort_key("a") < grammar.cell_sort_key("b")
        assert grammar.cell_sort_key("A") < grammar.cell_sort_key("a")  # 0x41 < 0x61

    def test_unit_request_object_compares_by_its_jcs_serialisation(self):
        low = {"currency": "EUR", "prices": "current"}
        high = {"currency": "USD", "prices": "current"}
        assert grammar.cell_sort_key(low) < grammar.cell_sort_key(high)

    def test_unit_forms_rank_null_ref_converted_composed(self):
        # `~` sorts after every JCS object by bytes, so only the rank can
        # put a ref first.
        cells = [
            None,
            "~",
            UnitRequest("ZZZ", "current"),
            ComposedUnit("A", "A"),
        ]
        keys = [grammar.cell_sort_key(cell) for cell in cells]
        assert keys == sorted(keys)
        assert len(set(keys)) == len(keys)

    def test_composed_units_compare_by_their_jcs_serialisation(self):
        # JCS writes `denominator` first, so it decides before `numerator`.
        low = ComposedUnit("Z", "A")
        high = ComposedUnit("A", "Z")
        assert grammar.cell_sort_key(low) < grammar.cell_sort_key(high)

    @pytest.mark.parametrize(
        "cell",
        [
            UnitRequest("EUR", "constant", 2020),
            ComposedUnit("USDollar", StatVarPart("dcid:Count_Person")),
        ],
    )
    def test_a_cell_and_its_json_form_share_one_key(self, cell):
        assert grammar.cell_sort_key(cell) == grammar.cell_sort_key(cell.as_json())


class TestRowSortKey:
    def test_excludes_value_and_status_columns(self):
        columns = ("stat_var", "time_period", "value", "unit", "status")
        row_a = ("sv", "2015", 1.0, "USD", None)
        row_b = ("sv", "2015", 2.0, "USD", None)
        # Same grain, different value -- keys must be equal.
        assert grammar.row_sort_key(columns, row_a) == grammar.row_sort_key(
            columns, row_b
        )

    def test_differs_when_a_non_excluded_cell_differs(self):
        columns = ("stat_var", "time_period", "value", "unit", "status")
        row_a = ("sv", "2015", 1.0, "USD", None)
        row_b = ("sv", "2016", 1.0, "USD", None)
        assert grammar.row_sort_key(columns, row_a) != grammar.row_sort_key(
            columns, row_b
        )
