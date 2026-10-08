"""Tests for Arrow-backed frame construction."""

from __future__ import annotations

import importlib.resources
import json
import random
import string
import unicodedata

import pytest

from dfo_frame_grammar import grammar
from dfo_frame_grammar.arrow import build_frame_from_arrow, read_frame_csv
from dfo_frame_grammar.columnar import unit_codes
from dfo_frame_grammar.content_version import frame_sha256
from dfo_frame_grammar.frame import Frame, canonical_frame_schema
from dfo_frame_grammar.grammar import FrameError

pyarrow = pytest.importorskip("pyarrow")

# Arrow cannot create this vector's large integer column.
_VALUE_UNREPRESENTABLE_IN_ARROW = "frame-value-integer-overflow-rejected"


def _vector_paths() -> list[importlib.resources.abc.Traversable]:
    root = importlib.resources.files("dfo_frame_grammar") / "vectors" / "frame"
    return sorted(
        (
            entry
            for entry in root.iterdir()
            if entry.name.endswith(".json") and entry.name != "frame-vector.schema.json"
        ),
        key=lambda entry: entry.name,
    )


def _load_vector(entry: importlib.resources.abc.Traversable) -> dict:
    with entry.open("rb") as f:
        return json.load(f)


_FRAME_VECTORS = [
    vector
    for vector in (_load_vector(path) for path in _vector_paths())
    if vector["kind"] == "frame"
]


_UNIT_REQUEST_TYPE = pyarrow.struct(
    [
        ("currency", pyarrow.string()),
        ("prices", pyarrow.string()),
        ("base_year", pyarrow.int64()),
    ]
)
_TWO_MEMBER_UNIT_TYPE = pyarrow.struct(
    [("ref", pyarrow.string()), ("request", _UNIT_REQUEST_TYPE)]
)
# The `unit` column's layout, spelled out member by member because consumers
# read these members by name.
_UNIT_TYPE = pyarrow.struct(
    [
        ("ref", pyarrow.string()),
        ("request", _UNIT_REQUEST_TYPE),
        ("composed", pyarrow.string()),
    ]
)
# A nested layout in which `composed` is a struct of numerator and denominator
# parts.
_NESTED_UNIT_PART_TYPE = pyarrow.struct(
    [
        ("ref", pyarrow.string()),
        ("request", _UNIT_REQUEST_TYPE),
        ("stat_var", pyarrow.string()),
    ]
)
_NESTED_UNIT_TYPE = pyarrow.struct(
    [
        ("ref", pyarrow.string()),
        ("request", _UNIT_REQUEST_TYPE),
        (
            "composed",
            pyarrow.struct(
                [
                    ("numerator", _NESTED_UNIT_PART_TYPE),
                    ("denominator", _NESTED_UNIT_PART_TYPE),
                ]
            ),
        ),
    ]
)


def _is_composed(unit: object) -> bool:
    return isinstance(unit, dict) and "numerator" in unit


def _tagged_unit(unit: object) -> object:
    """A JSON `unit` cell as the tagged struct value Arrow carries it in. A
    composed unit's text is `json.dumps` output, which is not JCS, so a
    frame built from it shows the builder re-canonicalises the text."""
    if unit is None:
        return None
    if isinstance(unit, str):
        return {"ref": unit}
    assert isinstance(unit, dict)
    if _is_composed(unit):
        return {"composed": json.dumps(unit)}
    return {"request": unit}


def _unit_table(units: pyarrow.Array) -> pyarrow.Table:
    """One row per unit, distinct by provider."""
    return pyarrow.table(
        {
            "stat_var": ["dcid:X"] * len(units),
            "provider": [f"country/{index:03d}" for index in range(len(units))],
            "time_period": ["2015"] * len(units),
            "value": [1.0] * len(units),
            "unit": units,
        }
    )


def _vector_to_table(vector: dict) -> pyarrow.Table:
    """Build a table from the vector's raw row keys and values.

    Arrow cannot infer one column type for a composed unit beside the
    other forms, so a positive vector that holds one carries its `unit`
    column in the tagged struct.
    """
    rows = vector["rows"]
    if not rows:
        try:
            columns = grammar.derive_columns(vector["role_keys"])
        except FrameError:
            # Invalid role keys fail before Arrow data is read.
            return pyarrow.table({})
        return pyarrow.table(
            {
                column: pyarrow.array([], type=pyarrow.string())
                for column in columns
                if column != grammar.STATUS_COLUMN
            }
        )
    raw_keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                raw_keys.append(key)
    if vector["classification"] == "positive" and any(
        _is_composed(row.get("unit")) for row in rows
    ):
        columns = {
            key: [row.get(key) for row in rows] for key in raw_keys if key != "unit"
        }
        columns["unit"] = pyarrow.array(
            [_tagged_unit(row.get("unit")) for row in rows], type=_UNIT_TYPE
        )
        return pyarrow.table(columns)
    return pyarrow.table({key: [row.get(key) for row in rows] for key in raw_keys})


class TestEveryVectorMatchesFrameBuild:
    @pytest.mark.parametrize(
        "vector", _FRAME_VECTORS, ids=[v["name"] for v in _FRAME_VECTORS]
    )
    def test_arrow_path_matches_frame_build(self, vector: dict) -> None:
        role_keys = vector["role_keys"]
        rows = vector["rows"]

        if vector["name"] == _VALUE_UNREPRESENTABLE_IN_ARROW:
            # Arrow rejects this integer while constructing the table.
            with pytest.raises((OverflowError, pyarrow.lib.ArrowInvalid)):
                _vector_to_table(vector)
            return

        table = _vector_to_table(vector)

        if vector["classification"] == "negative":
            with pytest.raises(FrameError) as excinfo:
                build_frame_from_arrow(table, role_keys=role_keys)
            assert excinfo.value.category == vector["failure_category"]
            return

        expected = Frame.build(role_keys=role_keys, rows=rows)
        got = build_frame_from_arrow(table, role_keys=role_keys)
        assert got == expected
        assert frame_sha256(got) == frame_sha256(expected)
        assert frame_sha256(got) == vector["expected_frame_sha256"]


def test_vector_suite_is_non_empty() -> None:
    assert len(_FRAME_VECTORS) >= 20, (
        f"expected at least 20 frame vectors, found {len(_FRAME_VECTORS)} -- "
        "the vector directory may be truncated"
    )


class TestEmptyTable:
    def test_zero_row_table_builds_a_columns_only_frame(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": pyarrow.array([], type=pyarrow.string()),
                "provider": pyarrow.array([], type=pyarrow.string()),
                "time_period": pyarrow.array([], type=pyarrow.string()),
                "value": pyarrow.array([], type=pyarrow.float64()),
            }
        )
        frame = build_frame_from_arrow(table, role_keys=["provider"])
        assert frame.rows == ()
        assert frame == Frame.build(role_keys=["provider"], rows=[])

    def test_untyped_zero_row_required_columns_build_a_columns_only_frame(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": [],
                "provider": [],
                "time_period": [],
                "value": [],
            }
        )

        frame = build_frame_from_arrow(table, role_keys=["provider"])

        assert frame.rows == ()
        assert frame == Frame.build(role_keys=["provider"], rows=[])


class TestSchemaRefusals:
    def test_status_column_present_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [1.0],
                "status": [None],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_unknown_column_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [1.0],
                "not_a_grammar_column": ["x"],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_missing_required_column_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "time_period": ["2015"],
                "value": [1.0],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_value_column_of_boolean_type_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": pyarrow.array([True], type=pyarrow.bool_()),
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_value_column_of_a_non_numeric_type_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": ["not-a-number"],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_null_value_cell_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": pyarrow.array([None], type=pyarrow.float64()),
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_non_empty_null_typed_required_text_column_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": pyarrow.array([None], type=pyarrow.null()),
                "time_period": ["2015"],
                "value": [1.0],
            }
        )

        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_non_finite_value_cell_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [float("inf")],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "non_finite_number"

    def test_ref_column_of_a_non_string_type_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": pyarrow.array([1], type=pyarrow.int64()),
                "time_period": ["2015"],
                "value": [1.0],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_time_period_column_of_a_non_string_type_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": pyarrow.array([2015], type=pyarrow.int64()),
                "value": [1.0],
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_required_dictionary_text_value_with_null_dictionary_entry_is_refused(
        self,
    ) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": pyarrow.DictionaryArray.from_arrays(
                    pyarrow.array([0], type=pyarrow.int8()),
                    pyarrow.array([None], type=pyarrow.string()),
                ),
                "time_period": ["2015"],
                "value": [1.0],
            }
        )

        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_required_dictionary_text_value_with_unused_null_entry_is_accepted(
        self,
    ) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": pyarrow.DictionaryArray.from_arrays(
                    pyarrow.array([1], type=pyarrow.int8()),
                    pyarrow.array([None, "country/FRA"], type=pyarrow.string()),
                ),
                "time_period": ["2015"],
                "value": [1.0],
            }
        )

        assert build_frame_from_arrow(table, role_keys=["provider"]) == Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:X",
                    "provider": "country/FRA",
                    "time_period": "2015",
                    "value": 1.0,
                }
            ],
        )

    def test_optional_dictionary_text_value_with_null_dictionary_entry_is_null(
        self,
    ) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [1.0],
                "measurementMethod": pyarrow.DictionaryArray.from_arrays(
                    pyarrow.array([0], type=pyarrow.int8()),
                    pyarrow.array([None], type=pyarrow.string()),
                ),
            }
        )

        assert build_frame_from_arrow(table, role_keys=["provider"]) == Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:X",
                    "provider": "country/FRA",
                    "time_period": "2015",
                    "value": 1.0,
                }
            ],
        )

    def test_inferred_null_optional_facet_is_unbound(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [1.0],
                "provenance": [None],
            }
        )

        assert build_frame_from_arrow(table, role_keys=["provider"]) == Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:X",
                    "provider": "country/FRA",
                    "time_period": "2015",
                    "value": 1.0,
                }
            ],
        )

    @pytest.mark.parametrize(
        ("column", "nested_values"),
        [
            pytest.param(
                "provider",
                pyarrow.array(
                    [{"id": "country/FRA"}],
                    type=pyarrow.struct([("id", pyarrow.string())]),
                ),
                id="struct-reference",
            ),
            pytest.param(
                "time_period",
                pyarrow.array([["2015"]], type=pyarrow.list_(pyarrow.string())),
                id="list-time-period",
            ),
        ],
    )
    def test_nested_reference_and_time_period_types_raise_frame_error(
        self, column: str, nested_values: pyarrow.Array
    ) -> None:
        columns: dict[str, object] = {
            "stat_var": ["dcid:X"],
            "provider": ["country/FRA"],
            "time_period": ["2015"],
            "value": [1.0],
        }
        columns[column] = nested_values
        table = pyarrow.table(columns)

        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    @pytest.mark.parametrize(
        ("column", "values"),
        [
            pytest.param(
                "provider",
                pyarrow.DictionaryArray.from_arrays(
                    pyarrow.array([0], type=pyarrow.int8()),
                    pyarrow.array(["country/FRA"]),
                ),
                id="dictionary-string-reference",
            ),
            pytest.param(
                "time_period",
                pyarrow.DictionaryArray.from_arrays(
                    pyarrow.array([0], type=pyarrow.int8()),
                    pyarrow.array(["2015"]),
                ),
                id="dictionary-string-time-period",
            ),
            pytest.param(
                "provider",
                pyarrow.array(["country/FRA"], type=pyarrow.string_view()),
                id="string-view-reference",
            ),
            pytest.param(
                "time_period",
                pyarrow.array(["2015"], type=pyarrow.string_view()),
                id="string-view-time-period",
            ),
        ],
    )
    def test_supported_arrow_text_types_build_a_frame(
        self, column: str, values: pyarrow.Array
    ) -> None:
        columns: dict[str, object] = {
            "stat_var": ["dcid:X"],
            "provider": ["country/FRA"],
            "time_period": ["2015"],
            "value": [1.0],
        }
        columns[column] = values

        frame = build_frame_from_arrow(pyarrow.table(columns), role_keys=["provider"])

        assert frame == Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:X",
                    "provider": "country/FRA",
                    "time_period": "2015",
                    "value": 1.0,
                }
            ],
        )

    def test_column_names_colliding_after_nfc_are_refused(self) -> None:
        # The precomposed and decomposed spellings both normalise to "état".
        table = pyarrow.Table.from_arrays(
            [
                pyarrow.array(["dcid:X"]),
                pyarrow.array(["2015"]),
                pyarrow.array([1.0]),
                pyarrow.array(["a"]),
                pyarrow.array(["b"]),
            ],
            names=["stat_var", "time_period", "value", "état", "état"],
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["état"])
        assert excinfo.value.category == "schema_violation"

    def test_unit_column_of_an_unsupported_arrow_type_is_refused(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [1.0],
                "unit": pyarrow.array([1], type=pyarrow.int64()),
            }
        )
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(table, role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_null_typed_unit_column_is_unbound_for_every_row(self) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"],
                "provider": ["country/FRA"],
                "time_period": ["2015"],
                "value": [1.0],
                "unit": pyarrow.array([None], type=pyarrow.null()),
            }
        )
        frame = build_frame_from_arrow(table, role_keys=["provider"])
        assert frame.rows[0][frame.columns.index("unit")] is None

    def test_unit_struct_row_with_a_null_struct_is_unbound(self) -> None:
        unit_type = pyarrow.struct(
            [("currency", pyarrow.string()), ("prices", pyarrow.string())]
        )
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X", "dcid:X"],
                "provider": ["country/FRA", "country/DEU"],
                "time_period": ["2015", "2016"],
                "value": [1.0, 2.0],
                "unit": pyarrow.array(
                    [None, {"currency": "USD", "prices": "current"}], type=unit_type
                ),
            }
        )
        frame = build_frame_from_arrow(table, role_keys=["provider"])
        unit_index = frame.columns.index("unit")
        units = [row[unit_index] for row in frame.rows]
        assert None in units
        assert any(isinstance(u, grammar.UnitRequest) for u in units)


class TestTaggedUnitStruct:
    _UNITS = (
        None,
        "USDollar",
        {"currency": "EUR", "prices": "constant", "base_year": 2020},
        {"currency": "USD", "prices": "current"},
        {"numerator": "USDollar", "denominator": {"stat_var": "dcid:Count_Person"}},
        {
            "numerator": {"currency": "EUR", "prices": "constant", "base_year": 2020},
            "denominator": {"stat_var": "dcid:Count_Person"},
        },
        {"numerator": "USDollar", "denominator": "Euro"},
    )

    def _expected(self, units: tuple[object, ...]) -> Frame:
        return Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:X",
                    "provider": f"country/{index:03d}",
                    "time_period": "2015",
                    "value": 1.0,
                    "unit": unit,
                }
                for index, unit in enumerate(units)
            ],
        )

    def test_the_unit_column_has_the_stated_layout(self) -> None:
        assert canonical_frame_schema(("unit",)).field("unit").type == _UNIT_TYPE

    def test_three_member_struct_loads_every_form(self) -> None:
        units = pyarrow.array(
            [_tagged_unit(unit) for unit in self._UNITS], type=_UNIT_TYPE
        )
        got = build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert got == self._expected(self._UNITS)

    def test_two_member_struct_loads_into_the_canonical_layout(self) -> None:
        held = self._UNITS[:4]
        units = pyarrow.array(
            [_tagged_unit(unit) for unit in held], type=_TWO_MEMBER_UNIT_TYPE
        )
        got = build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert got.table.schema.field("unit").type == _UNIT_TYPE
        assert got == self._expected(held)

    def test_part_strings_are_nfc_normalised(self) -> None:
        unit = {
            "numerator": "unit/e\u0301",
            "denominator": {"stat_var": "dcid:e\u0301"},
        }
        units = pyarrow.array([_tagged_unit(unit)], type=_UNIT_TYPE)
        got = build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert got.rows[0][got.columns.index("unit")] == grammar.ComposedUnit(
            "unit/\u00e9", grammar.StatVarPart("dcid:\u00e9")
        )

    @pytest.mark.parametrize(
        ("unit", "category"),
        [
            pytest.param({"composed": "a per b"}, "invalid_unit", id="not-json"),
            pytest.param(
                {"composed": "[" * 100_000}, "invalid_unit", id="nested-past-recursion"
            ),
            pytest.param(
                {"composed": '{"numerator": ' + "9" * 5_000 + ', "denominator": "a"}'},
                "invalid_unit",
                id="integer-past-digit-limit",
            ),
            pytest.param({"composed": '"USDollar"'}, "invalid_unit", id="json-ref"),
            pytest.param(
                {"composed": '{"currency": "USD", "prices": "current"}'},
                "invalid_unit",
                id="json-request",
            ),
            pytest.param({"composed": "null"}, "invalid_unit", id="json-null"),
            pytest.param(
                {"composed": '{"numerator": "USDollar", "denominator": null}'},
                "invalid_unit",
                id="null-part",
            ),
            pytest.param(
                {
                    "composed": '{"numerator": "USDollar", '
                    '"denominator": {"stat_var": "Count_Person"}}'
                },
                "invalid_unit",
                id="stat-var-without-prefix",
            ),
            pytest.param(
                {
                    "composed": '{"numerator": "USDollar", '
                    '"denominator": {"currency": "usd", "prices": "current"}}'
                },
                "invalid_unit",
                id="invalid-unit-request-part",
            ),
            pytest.param(
                {
                    "composed": '{"numerator": {"stat_var": "dcid:X", '
                    '"prices": "current"}, "denominator": "Euro"}'
                },
                "invalid_unit",
                id="part-with-two-variants",
            ),
            pytest.param(
                {
                    "composed": '{"numerator": "USDollar", "numerator": "Euro", '
                    '"denominator": "Euro"}'
                },
                "invalid_unit",
                id="repeated-member",
            ),
            pytest.param(
                {"composed": '{"numerator": "\\ud800", "denominator": "Euro"}'},
                "lone_surrogate",
                id="lone-surrogate-escape",
            ),
            pytest.param(
                {
                    "ref": "USDollar",
                    "composed": '{"numerator": "USDollar", "denominator": "Euro"}',
                },
                "schema_violation",
                id="cell-with-two-variants",
            ),
        ],
    )
    def test_malformed_tagged_composed_unit_refuses(
        self, unit: dict, category: str
    ) -> None:
        units = pyarrow.array([unit], type=_UNIT_TYPE)
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert excinfo.value.category == category

    @pytest.mark.parametrize(
        "units",
        [
            pytest.param(
                pyarrow.array(
                    [{"ref": {"numerator": "a", "denominator": "b"}, "request": None}],
                    type=pyarrow.struct(
                        [
                            (
                                "ref",
                                pyarrow.struct(
                                    [
                                        ("numerator", pyarrow.string()),
                                        ("denominator", pyarrow.string()),
                                    ]
                                ),
                            ),
                            ("request", _UNIT_REQUEST_TYPE),
                        ]
                    ),
                ),
                id="ref-typed-as-a-struct",
            ),
            pytest.param(
                pyarrow.array(
                    [{"composed": {"numerator": "a", "denominator": "b"}}],
                    type=pyarrow.struct(
                        [
                            ("ref", pyarrow.string()),
                            ("request", _UNIT_REQUEST_TYPE),
                            (
                                "composed",
                                pyarrow.struct(
                                    [
                                        ("numerator", pyarrow.string()),
                                        ("denominator", pyarrow.string()),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                id="composed-sides-typed-as-strings",
            ),
            pytest.param(
                pyarrow.array([None, {"ref": "USDollar"}], type=_NESTED_UNIT_TYPE),
                id="composed-typed-as-the-nested-struct-holding-none",
            ),
            pytest.param(
                pyarrow.array(
                    [{"composed": 7}],
                    type=pyarrow.struct(
                        [
                            ("ref", pyarrow.string()),
                            ("request", _UNIT_REQUEST_TYPE),
                            ("composed", pyarrow.int64()),
                        ]
                    ),
                ),
                id="composed-typed-as-an-integer",
            ),
        ],
    )
    def test_tagged_member_of_the_wrong_type_refuses(
        self, units: pyarrow.Array
    ) -> None:
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert excinfo.value.category == "schema_violation"

    def test_null_typed_composed_member_loads(self) -> None:
        units = pyarrow.array(
            [{"ref": "USDollar", "request": None, "composed": None}, None]
        )
        assert units.type.field("composed").type == pyarrow.null()
        got = build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert got == self._expected(("USDollar", None))

    def test_spellings_of_one_composed_unit_share_a_code(self) -> None:
        units = pyarrow.array(
            [
                {"composed": '{"numerator":"USDollar","denominator":"Euro"}'},
                {"composed": '{ "denominator" : "Euro", "numerator" : "USDollar" }'},
            ],
            type=_UNIT_TYPE,
        )
        got = build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert unit_codes(got.table.column("unit")).count == 1

    def test_untagged_struct_naming_composed_members_is_a_bare_request(self) -> None:
        units = pyarrow.array([{"numerator": "USDollar", "denominator": "Euro"}])
        with pytest.raises(FrameError) as excinfo:
            build_frame_from_arrow(_unit_table(units), role_keys=["provider"])
        assert excinfo.value.category == "invalid_unit"


_UNICODE_ALPHABET = [*list(string.ascii_letters), "é", "é", "å", "å"]


def _random_ref(rng: random.Random, *, prefix: str = "") -> str:
    length = rng.randint(1, 6)
    return prefix + "".join(rng.choice(_UNICODE_ALPHABET) for _ in range(length))


def _random_row(rng: random.Random, role_keys: list[str]) -> dict[str, object]:
    row: dict[str, object] = {
        "stat_var": "dcid:" + _random_ref(rng),
        "time_period": str(rng.randint(1990, 2030)),
        "value": rng.choice(
            [-0.0, 0.0, rng.uniform(-1e9, 1e9), rng.randint(-1000, 1000)]
        ),
    }
    for role in role_keys:
        row[role] = "country/" + _random_ref(rng)
    # Sparse facets: each optional axis is present about half the time.
    if rng.random() < 0.5:
        row["unit"] = _random_ref(rng, prefix="unit/")
    if rng.random() < 0.5:
        row["measurementMethod"] = _random_ref(rng, prefix="method/")
    if rng.random() < 0.5:
        row["provenance"] = _random_ref(rng, prefix="prov/")
    return row


def _rows_to_table(
    role_keys: list[str], rows: list[dict[str, object]]
) -> pyarrow.Table:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return pyarrow.table({key: [row.get(key) for row in rows] for key in keys})


class TestRandomEquivalence:
    """A fixed, seeded sample of generated tables."""

    @pytest.mark.parametrize("seed", range(25))
    def test_generated_table_matches_frame_build(self, seed: int) -> None:
        rng = random.Random(seed)
        role_keys = rng.sample(
            [
                "provider",
                "recipient",
                "financingProvider",
                "financingRecipient",
                "état",
            ],
            k=rng.randint(1, 4),
        )
        row_count = rng.randint(1, 40)
        rows = [_random_row(rng, role_keys) for _ in range(row_count)]

        table = _rows_to_table(role_keys, rows)
        expected = Frame.build(role_keys=role_keys, rows=rows)
        got = build_frame_from_arrow(table, role_keys=role_keys)

        assert got == expected
        assert frame_sha256(got) == frame_sha256(expected)

    def test_generated_duplicate_grain_is_refused_on_both_paths(self) -> None:
        rng = random.Random(4242)
        role_keys = ["provider"]
        row = _random_row(rng, role_keys)
        # A second row shares every grain cell.
        duplicate = dict(row)
        duplicate["value"] = rng.uniform(-1e9, 1e9)
        rows = [row, duplicate]

        table = _rows_to_table(role_keys, rows)
        with pytest.raises(FrameError) as arrow_excinfo:
            build_frame_from_arrow(table, role_keys=role_keys)
        with pytest.raises(FrameError) as build_excinfo:
            Frame.build(role_keys=role_keys, rows=rows)
        assert arrow_excinfo.value.category == build_excinfo.value.category
        assert arrow_excinfo.value.category == "duplicate_row_key"


class TestFusedNormalisationRunsOncePerDistinctValue:
    """Normalisation and surrogate checks run once per distinct value."""

    def test_normalize_and_lone_surrogate_check_are_bounded_by_distinct_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        role_keys = ["provider"]
        # Co-prime cardinalities produce each stat_var/provider pair per year.
        distinct_stat_vars = [f"dcid:StatVar_{i}" for i in range(4)] + [
            "dcid:" + unicodedata.normalize("NFD", "café")
        ]
        distinct_providers = [f"country/P{i}" for i in range(2)] + [
            unicodedata.normalize("NFD", "über")
        ]
        block_size = len(distinct_stat_vars) * len(distinct_providers)
        row_count = block_size * 20
        rows = [
            {
                "stat_var": distinct_stat_vars[i % len(distinct_stat_vars)],
                "provider": distinct_providers[i % len(distinct_providers)],
                "time_period": str(1990 + i // block_size),
                "value": float(i),
            }
            for i in range(row_count)
        ]
        table = _rows_to_table(role_keys, rows)
        distinct_time_periods = row_count // block_size
        distinct_count = (
            len(distinct_stat_vars) + len(distinct_providers) + distinct_time_periods
        )

        normalize_calls = 0
        original_normalize = unicodedata.normalize

        def counting_normalize(form: str, value: str) -> str:
            nonlocal normalize_calls
            normalize_calls += 1
            return original_normalize(form, value)

        surrogate_calls = 0
        original_reject = grammar.reject_lone_surrogates

        def counting_reject(text: str) -> None:
            nonlocal surrogate_calls
            surrogate_calls += 1
            original_reject(text)

        monkeypatch.setattr(unicodedata, "normalize", counting_normalize)
        monkeypatch.setattr(grammar, "reject_lone_surrogates", counting_reject)

        frame = build_frame_from_arrow(table, role_keys=role_keys)

        assert len(frame.rows) == row_count
        # Role-key validation adds a fixed number of calls.
        role_key_overhead = 2 * len(role_keys)
        assert normalize_calls <= distinct_count + role_key_overhead
        assert normalize_calls < row_count
        assert surrogate_calls <= distinct_count + role_key_overhead
        assert surrogate_calls < row_count

        expected = Frame.build(role_keys=role_keys, rows=rows)
        assert frame == expected


class TestReadFrameCsv:
    """CSV input uses the grammar's column names."""

    def test_csv_bytes_round_trip_matches_frame_build(self) -> None:
        role_keys = ["provider", "recipient"]
        rows = [
            {
                "stat_var": "dcid:MalariaGrantDisbursements",
                "provider": "country/FRA",
                "recipient": "country/TGO",
                "time_period": "2015",
                "value": 1000.0,
                "unit": "USD",
            },
            {
                "stat_var": "dcid:MalariaGrantDisbursements",
                "provider": "country/DEU",
                "recipient": "country/KEN",
                "time_period": "2016",
                "value": -0.0,
            },
        ]
        header = "stat_var,provider,recipient,time_period,value,unit\n"
        lines = [
            ",".join(
                [
                    row["stat_var"],
                    row["provider"],
                    row["recipient"],
                    row["time_period"],
                    repr(row["value"]),
                    row.get("unit") or "",
                ]
            )
            for row in rows
        ]
        csv_bytes = (header + "\n".join(lines) + "\n").encode("utf-8")

        table = read_frame_csv(csv_bytes, role_keys=role_keys)
        got = build_frame_from_arrow(table, role_keys=role_keys)
        expected = Frame.build(role_keys=role_keys, rows=rows)
        assert got == expected

    def test_blank_optional_cell_reads_as_unbound_not_empty_string(self) -> None:
        csv_bytes = (
            b"stat_var,provider,time_period,value,unit\ndcid:X,country/FRA,2015,1000,\n"
        )
        table = read_frame_csv(csv_bytes, role_keys=["provider"])
        assert table.column("unit").to_pylist() == [None]


class TestCanonicalArrowStorage:
    def test_direct_builder_does_not_materialise_rows_or_call_python_sort_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X", "dcid:X"],
                "provider": ["country/FRA", "country/DEU"],
                "time_period": ["2016", "2015"],
                "value": [1.0, 2.0],
            }
        )

        def fail(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("direct Arrow construction used a compatibility path")

        monkeypatch.setattr(grammar, "row_sort_key", fail)
        monkeypatch.setattr(Frame, "rows", property(fail))
        frame = build_frame_from_arrow(table, role_keys=["provider"])
        assert frame.table.num_rows == 2

    def test_sorting_allows_role_key_matching_an_auxiliary_column_name(self) -> None:
        rows = [
            {
                "stat_var": "dcid:X",
                "__frame_sort_0_payload": "country/FRA",
                "time_period": "2016",
                "value": 1.0,
            },
            {
                "stat_var": "dcid:X",
                "__frame_sort_0_payload": "country/DEU",
                "time_period": "2015",
                "value": 2.0,
            },
        ]

        assert build_frame_from_arrow(
            _rows_to_table(["__frame_sort_0_payload"], rows),
            role_keys=["__frame_sort_0_payload"],
        ) == Frame.build(role_keys=["__frame_sort_0_payload"], rows=rows)

    def test_chunk_layout_and_builders_share_table_equality_and_hash(self) -> None:
        rows = [
            {
                "stat_var": "dcid:X",
                "provider": "country/FRA",
                "time_period": "2015",
                "value": -0.0,
            },
            {
                "stat_var": "dcid:X",
                "provider": "country/DEU",
                "time_period": "2016",
                "value": 2,
            },
        ]
        expected = Frame.build(role_keys=["provider"], rows=rows)
        chunks = pyarrow.table(
            {
                name: pyarrow.chunked_array(
                    [
                        expected.table.column(name).slice(0, 1),
                        expected.table.column(name).slice(1),
                    ]
                )
                for name in expected.columns
                if name != "status"
            }
        )
        got = build_frame_from_arrow(chunks, role_keys=["provider"])
        assert got == expected
        assert hash(got) == hash(expected)

    def test_unit_order_uses_rank_then_exact_jcs_payload(self) -> None:
        unit_type = pyarrow.struct(
            [
                ("currency", pyarrow.string()),
                ("prices", pyarrow.string()),
                ("base_year", pyarrow.int64()),
            ]
        )
        table = pyarrow.table(
            {
                "stat_var": ["dcid:X"] * 2,
                "provider": ["country/FRA"] * 2,
                "time_period": ["2015"] * 2,
                "value": list(range(2)),
                "unit": pyarrow.array(
                    [
                        {"currency": "USD", "prices": "constant", "base_year": 2},
                        {"currency": "USD", "prices": "constant", "base_year": 10},
                    ],
                    type=unit_type,
                ),
            }
        )
        # Arrow cannot mix string and request structs in a source column;
        # verify the request lexical trap directly, and the complete rank
        # ordering through Frame.build's matching canonical table.
        requests = build_frame_from_arrow(table, role_keys=["provider"])
        assert [row[4].base_year for row in requests.rows] == [10, 2]
        mixed = Frame.build(
            role_keys=["provider"],
            rows=[
                {
                    "stat_var": "dcid:X",
                    "provider": "country/FRA",
                    "time_period": "2015",
                    "value": 0,
                    "unit": unit,
                }
                for unit in [
                    None,
                    "ZZZ",
                    "AAA",
                    {"currency": "USD", "prices": "constant", "base_year": 2},
                    {"currency": "USD", "prices": "constant", "base_year": 10},
                ]
            ],
        )
        assert [row[4] for row in mixed.rows[:3]] == [None, "AAA", "ZZZ"]
        source = mixed.table.select(
            [name for name in mixed.columns if name != "status"]
        )
        assert build_frame_from_arrow(source, role_keys=["provider"]) == mixed
