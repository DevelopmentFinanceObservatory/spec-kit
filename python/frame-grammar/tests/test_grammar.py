"""Column derivation, cell-domain validators, and the row-sort collation
(`dfo_frame_grammar.grammar`) -- the pinned rules from `docs/frame-grammar-v1.md`'s
"The frame" section, exercised directly against the pure functions rather
than through a built `Frame`.
"""

from __future__ import annotations

import pytest

from dfo_frame_grammar import grammar
from dfo_frame_grammar.grammar import FrameError


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
        # concrete currency: the role shape is only valid on the
        # content-version facet ref path (validate_unit_request_ref).
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request(
                {"currency": {"role": "origin"}, "prices": "current"}
            )
        assert excinfo.value.category == "invalid_unit"


class TestValidateUnitRequestRef:
    """`validate_unit_request_ref` accepts the same shape
    `validate_unit_request` does, plus a role-shaped `currency`. This is the
    entry point for content-version facet refs; `validate_unit_request` is for
    frame per-row `unit` cells."""

    @pytest.mark.parametrize("role", ["observationAbout", "origin", "destination"])
    def test_accepts_each_role_key(self, role):
        grammar.validate_unit_request_ref(
            {"currency": {"role": role}, "prices": "current"}
        )  # must not raise

    def test_accepts_role_with_constant_prices_and_base_year(self):
        grammar.validate_unit_request_ref(
            {
                "currency": {"role": "origin"},
                "prices": "constant",
                "base_year": 2020,
            }
        )  # must not raise

    def test_accepts_concrete_currency_string(self):
        grammar.validate_unit_request_ref(
            {"currency": "USD", "prices": "current"}
        )  # must not raise

    def test_refuses_unrecognised_role(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request_ref(
                {"currency": {"role": "financingProvider"}, "prices": "current"}
            )
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_role_object_with_extra_member(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request_ref(
                {
                    "currency": {"role": "origin", "extra": "x"},
                    "prices": "current",
                }
            )
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_lower_case_role(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request_ref(
                {"currency": {"role": "origin".upper()}, "prices": "current"}
            )
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_non_string_role(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request_ref(
                {"currency": {"role": 1}, "prices": "current"}
            )
        assert excinfo.value.category == "invalid_unit"

    def test_refuses_currency_that_is_neither_string_nor_object(self):
        with pytest.raises(FrameError) as excinfo:
            grammar.validate_unit_request_ref({"currency": 1, "prices": "current"})
        assert excinfo.value.category == "invalid_unit"


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
