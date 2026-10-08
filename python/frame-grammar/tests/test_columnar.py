"""`Frame.from_table`, `Frame.num_rows`, `Frame.__hash__`, and the kernels in
`dfo_frame_grammar.columnar`: unit codes, distinct-unit mapping, and the
grammar collation sort keys, each checked against the row-at-a-time
behaviour it replaces."""

from __future__ import annotations

import random

import pyarrow as pa
import pytest

from dfo_frame_grammar import arrow as arrow_module
from dfo_frame_grammar import grammar
from dfo_frame_grammar.arrow import build_frame_from_arrow
from dfo_frame_grammar.columnar import (
    array_kernel,
    collation_keys,
    collation_sort_keys,
    map_units,
    unit_cells,
    unit_codes,
)
from dfo_frame_grammar.frame import (
    Frame,
    canonical_frame_bytes,
    canonical_frame_schema,
    unit_cell_array,
    widen_frame_table,
)
from dfo_frame_grammar.grammar import FrameError

_UNIT_REQUEST_TYPE = pa.struct(
    [
        ("currency", pa.string()),
        ("prices", pa.string()),
        ("base_year", pa.int64()),
    ]
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stat_var": "dcid:X",
        "provider": "country/FRA",
        "time_period": "2015",
        "value": 1.0,
    }
    row.update(overrides)
    return row


def _mixed_unit_frame() -> Frame:
    units = [
        None,
        "ZZZ",
        "AAA",
        {"currency": "USD", "prices": "constant", "base_year": 2},
        {"currency": "USD", "prices": "constant", "base_year": 10},
        {"currency": "EUR", "prices": "current"},
        {"numerator": "ZZZ", "denominator": {"stat_var": "dcid:A"}},
        {"numerator": "AAA", "denominator": {"stat_var": "dcid:Z"}},
        {
            "numerator": {"currency": "USD", "prices": "constant", "base_year": 10},
            "denominator": "AAA",
        },
    ]
    rows = [
        _row(unit=unit, time_period=str(2000 + year))
        for unit in units
        for year in range(3)
    ]
    return Frame.build(role_keys=["provider"], rows=rows)


class TestFromTable:
    def test_wraps_a_canonical_table(self) -> None:
        built = _mixed_unit_frame()
        wrapped = Frame.from_table(columns=built.columns, table=built.table)
        assert wrapped == built

    def test_refuses_a_table_whose_schema_differs(self) -> None:
        built = _mixed_unit_frame()
        value_index = built.columns.index(grammar.VALUE_COLUMN)
        loose = built.table.set_column(
            value_index,
            grammar.VALUE_COLUMN,
            built.table.column(value_index).cast(pa.float32()),
        )
        with pytest.raises(FrameError) as excinfo:
            Frame.from_table(columns=built.columns, table=loose)
        assert excinfo.value.category == "schema_violation"

    @pytest.mark.parametrize(
        "columns",
        [
            ("provider", "stat_var", "time_period", "value", "unit"),
            (
                "stat_var",
                "recipient",
                "provider",
                "time_period",
                "value",
                "unit",
                "measurementMethod",
                "provenance",
                "status",
            ),
            ("stat_var", "time_period", "value", "unit", "status"),
        ],
        ids=["reserved-out-of-order", "roles-unsorted", "facets-missing"],
    )
    def test_refuses_columns_that_are_not_a_derived_order(
        self, columns: tuple[str, ...]
    ) -> None:
        schema = canonical_frame_schema(columns)
        with pytest.raises(FrameError) as excinfo:
            Frame.from_table(columns=columns, table=schema.empty_table())
        assert excinfo.value.category == "schema_violation"


class TestNumRowsAndHash:
    def test_neither_reads_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frame = _mixed_unit_frame()

        def fail(_self: Frame) -> object:
            raise AssertionError("read Frame.rows")

        monkeypatch.setattr(Frame, "rows", property(fail))
        assert frame.num_rows == 27
        hash(frame)

    def test_equal_frames_with_different_chunking_hash_equal(self) -> None:
        frame = _mixed_unit_frame()
        rechunked = Frame.from_table(
            columns=frame.columns,
            table=pa.concat_tables([frame.table.slice(0, 5), frame.table.slice(5)]),
        )
        assert rechunked == frame
        assert hash(rechunked) == hash(frame)

    def test_equal_frames_rebuilt_from_batches_hash_equal(self) -> None:
        frame = _mixed_unit_frame()
        batched = Frame.from_table(
            columns=frame.columns,
            table=pa.Table.from_batches(frame.table.to_batches(max_chunksize=4)),
        )
        assert batched.table.column(0).num_chunks > 1
        assert batched == frame
        assert hash(batched) == hash(frame)

    def test_distinct_one_row_frames_of_one_shape_hash_apart(self) -> None:
        frames = [
            Frame.build(role_keys=["provider"], rows=[_row(value=float(index))])
            for index in range(2000)
        ]
        assert len({hash(frame) for frame in frames}) == len(frames)

    def test_unit_cells_hash_by_content(self) -> None:
        units = [
            None,
            "USD",
            {"currency": "USD", "prices": "constant", "base_year": 2015},
            {"currency": "USD", "prices": "constant", "base_year": 2020},
        ]
        frames = [
            Frame.build(role_keys=["provider"], rows=[_row(unit=unit)])
            for unit in units
        ]
        assert len({hash(frame) for frame in frames}) == len(frames)

    def test_an_empty_frame_hashes(self) -> None:
        frame = Frame.build(role_keys=["provider"], rows=[])
        assert hash(frame) == hash(Frame.build(role_keys=["provider"], rows=[]))


def _random_unit_request_array(rng: random.Random, size: int) -> pa.Array:
    """A unit-request struct with top-level nulls, null leaves, and stale
    child values under a null parent."""
    currencies = [rng.choice(["USD", "EUR", None]) for _ in range(size)]
    prices = [rng.choice(["current", "constant", None]) for _ in range(size)]
    years = [rng.choice([2, 10, None]) for _ in range(size)]
    mask = pa.array([rng.random() < 0.2 for _ in range(size)])
    return pa.StructArray.from_arrays(
        [
            pa.array(currencies, pa.string()),
            pa.array(prices, pa.string()),
            pa.array(years, pa.int64()),
        ],
        fields=list(_UNIT_REQUEST_TYPE),
        mask=mask,
    )


class TestUnitCodes:
    @pytest.mark.parametrize("seed", range(20))
    def test_codes_agree_with_to_pylist(self, seed: int) -> None:
        rng = random.Random(seed)
        values = _random_unit_request_array(rng, rng.randint(0, 80))
        codes = unit_codes(values)
        python = values.to_pylist()
        code_list = codes.codes.to_pylist()

        first_seen: dict[object, int] = {}
        for index, value in enumerate(python):
            first_seen.setdefault(repr(value), index)
        assert codes.count == len(first_seen)
        assert codes.first_rows.to_pylist() == sorted(first_seen.values())
        for index, value in enumerate(python):
            assert code_list[index] == code_list[first_seen[repr(value)]]
        assert list(dict.fromkeys(code_list)) == list(range(codes.count))

    def test_canonical_unit_column(self) -> None:
        frame = _mixed_unit_frame()
        unit = frame.table.column(grammar.UNIT_COLUMN)
        codes = unit_codes(unit)
        assert codes.count == 9
        cells = unit_cells(unit, codes)
        rows = [row[frame.columns.index(grammar.UNIT_COLUMN)] for row in frame.rows]
        assert [cells[code] for code in codes.codes.to_pylist()] == rows

    def test_composed_units_code_by_value(self) -> None:
        person = grammar.StatVarPart("dcid:Count_Person")
        cells = [
            grammar.ComposedUnit("USDollar", person),
            grammar.ComposedUnit("USDollar", "dcid:Count_Person"),
            grammar.ComposedUnit("USDollar", person),
            grammar.ComposedUnit(person, "USDollar"),
            grammar.ComposedUnit(grammar.UnitRequest("USD", "current"), person),
            "USDollar",
            grammar.ComposedUnit("USDollar", person),
        ]
        unit = pa.concat_arrays([unit_cell_array(cell, 1) for cell in cells])
        codes = unit_codes(unit)
        assert codes.codes.to_pylist() == [0, 1, 0, 2, 3, 4, 0]
        assert unit_cells(unit, codes) == list(dict.fromkeys(cells))

    def test_an_all_null_child_keeps_null_structs_distinct(self) -> None:
        inner = pa.struct([("x", pa.string())])
        values = pa.array(
            [
                None,
                {"a": None, "inner": None},
                {"a": None, "inner": {"x": None}},
                {"a": None, "inner": None},
                {"a": None, "inner": {"x": None}},
                None,
            ],
            pa.struct([("a", pa.string()), ("inner", inner)]),
        )
        assert unit_codes(values).codes.to_pylist() == [0, 1, 2, 1, 2, 0]
        all_null = pa.array([None, {"a": None, "inner": None}, None], values.type)
        assert unit_codes(all_null).codes.to_pylist() == [0, 1, 0]
        seen: list[object] = []
        map_units(values, seen.append, result_type=pa.null())
        assert seen == values.to_pylist()[:3]

    def test_an_all_null_unhashed_child_gets_one_code_per_row(self) -> None:
        values = pa.array(
            [{"a": "x", "items": None}, {"a": "x", "items": None}],
            pa.struct([("a", pa.string()), ("items", pa.list_(pa.int64()))]),
        )
        assert unit_codes(values).codes.to_pylist() == [0, 1]

    def test_signed_zero_floats_stay_distinct(self) -> None:
        codes = unit_codes(pa.array([0.0, -0.0, 0.0, None]))
        assert codes.codes.to_pylist() == [0, 1, 0, 2]

    def test_an_unhashed_leaf_type_gets_one_code_per_row(self) -> None:
        values = pa.array([[1], [1], None], pa.list_(pa.int64()))
        assert unit_codes(values).codes.to_pylist() == [0, 1, 2]

    def test_a_dictionary_column_codes_by_value(self) -> None:
        values = pa.array(["b", "a", "b", None]).dictionary_encode()
        assert unit_codes(values).codes.to_pylist() == [0, 1, 0, 2]

    def test_empty(self) -> None:
        codes = unit_codes(pa.array([], _UNIT_REQUEST_TYPE))
        assert codes.count == 0
        assert len(codes.codes) == 0

    def test_map_units_calls_once_per_distinct_value(self) -> None:
        values = pa.array(["a", "b", "a", None, "b"] * 100)
        seen: list[object] = []

        def upper(value: object) -> object:
            seen.append(value)
            return None if value is None else str(value).upper()

        mapped = map_units(values, upper, result_type=pa.string())
        assert seen == ["a", "b", None]
        assert mapped.to_pylist() == ["A", "B", "A", None, "B"] * 100


class TestUnitCellArray:
    @pytest.mark.parametrize(
        "cell",
        [
            None,
            "USDollar",
            grammar.UnitRequest("EUR", "constant", 2020),
            grammar.UnitRequest("USD", "current"),
            grammar.ComposedUnit(
                grammar.UnitRequest("EUR", "constant", 2020),
                grammar.StatVarPart("dcid:Count_Person"),
            ),
            grammar.ComposedUnit("USDollar", "Euro"),
        ],
    )
    @pytest.mark.parametrize("length", [0, 3])
    def test_matches_the_column_frame_build_gives_the_cell(
        self, cell: grammar.UnitCell | None, length: int
    ) -> None:
        frame = Frame.build(
            role_keys=["provider"],
            rows=[
                _row(unit=grammar.unit_cell_to_json(cell), time_period=str(2000 + year))
                for year in range(length)
            ],
        )
        array = unit_cell_array(cell, length)
        assert array.type == canonical_frame_schema(("unit",)).field("unit").type
        assert array.equals(frame.table.column("unit").combine_chunks())

    @pytest.mark.parametrize(
        "cell",
        [
            pytest.param(
                grammar.ComposedUnit(grammar.ComposedUnit("a", "b"), "c"),
                id="composed-side",
            ),
            pytest.param(grammar.StatVarPart("dcid:X"), id="top-level-stat-var-part"),
            pytest.param(
                grammar.ComposedUnit(grammar.StatVarPart("Count_Person"), "c"),
                id="stat-var-without-prefix",
            ),
            pytest.param(grammar.ComposedUnit("", "c"), id="empty-ref-side"),
            pytest.param(grammar.ComposedUnit("a", None), id="null-side"),
            pytest.param(grammar.ComposedUnit(7, "Euro"), id="int-side"),
            pytest.param(
                grammar.ComposedUnit({"currency": "USD", "prices": "current"}, "Euro"),
                id="json-form-side",
            ),
            pytest.param(grammar.ComposedUnit("Euro", ["USDollar"]), id="list-side"),
            pytest.param(
                grammar.ComposedUnit(grammar.StatVarPart(7), "Euro"),
                id="stat-var-not-a-string",
            ),
            pytest.param(grammar.UnitRequest(7, "current"), id="currency-not-a-string"),
            pytest.param(
                grammar.UnitRequest("USD", "constant", "2020"), id="base-year-a-string"
            ),
            pytest.param(grammar.UnitRequest("usd", "current"), id="invalid-request"),
            pytest.param("", id="empty-ref"),
            pytest.param("unit/e\u0301", id="non-nfc-ref"),
            pytest.param({"currency": "USD", "prices": "current"}, id="json-form"),
        ],
    )
    def test_refuses_a_cell_validation_would_not_return(self, cell: object) -> None:
        with pytest.raises(FrameError) as excinfo:
            unit_cell_array(cell, 1)  # ty: ignore[invalid-argument-type]
        assert excinfo.value.category == "invalid_unit"


class TestUnitColumnBytes:
    def test_ref_rows_cost_within_5_bytes_a_row_of_the_two_member_layout(
        self,
    ) -> None:
        # The difference pins the layout; absolute `nbytes` depends on
        # builder choices. `pa.array` gives every row its own buffers.
        rows = [{"ref": "USDollar"}] * 10_000
        two_member = pa.struct([("ref", pa.string()), ("request", _UNIT_REQUEST_TYPE)])
        canonical = canonical_frame_schema(("unit",)).field("unit").type
        extra = pa.array(rows, canonical).nbytes - pa.array(rows, two_member).nbytes
        assert extra <= 5 * len(rows)


class TestWidenFrameTable:
    """A table stored in the two-member layout of the `unit` struct, rebuilt
    here from plain values, against the frame the same rows build."""

    _TWO_MEMBER = pa.struct([("ref", pa.string()), ("request", _UNIT_REQUEST_TYPE)])

    def _frame(self) -> Frame:
        units = [
            None,
            "USDollar",
            {"currency": "EUR", "prices": "constant", "base_year": 2020},
            {"currency": "USD", "prices": "current"},
        ]
        return Frame.build(
            role_keys=["provider"],
            rows=[
                _row(unit=unit, time_period=str(2000 + year))
                for year, unit in enumerate(units)
            ],
        )

    def _stored(self, frame: Frame) -> pa.Table:
        position = frame.columns.index(grammar.UNIT_COLUMN)
        units = pa.array(
            [
                None,
                {"ref": "USDollar", "request": None},
                {
                    "ref": None,
                    "request": {
                        "currency": "EUR",
                        "prices": "constant",
                        "base_year": 2020,
                    },
                },
                {"ref": None, "request": {"currency": "USD", "prices": "current"}},
            ],
            type=self._TWO_MEMBER,
        )
        return frame.table.set_column(position, grammar.UNIT_COLUMN, units)

    @pytest.mark.parametrize("offset", [0, 1])
    def test_two_member_table_widens_to_the_frame_built_from_rows(
        self, offset: int
    ) -> None:
        frame = self._frame()
        expected = Frame.from_table(
            columns=frame.columns, table=frame.table.slice(offset)
        )
        stored = self._stored(frame).slice(offset)
        with pytest.raises(FrameError):
            Frame.from_table(columns=frame.columns, table=stored)
        widened = Frame.from_table(
            columns=frame.columns, table=widen_frame_table(stored)
        )
        assert widened == expected
        assert canonical_frame_bytes(widened) == canonical_frame_bytes(expected)

    def test_current_schema_is_returned_unchanged(self) -> None:
        table = _mixed_unit_frame().table
        assert widen_frame_table(table) is table

    def test_any_other_schema_refuses(self) -> None:
        stored = self._stored(self._frame())
        for table in (
            stored.drop_columns(["status"]),
            stored.set_column(
                stored.schema.get_field_index("value"),
                "value",
                pa.array([1, 2, 3, 4], pa.int64()),
            ),
            stored.set_column(
                stored.schema.get_field_index("unit"),
                "unit",
                pa.array(["USDollar"] * 4),
            ),
        ):
            with pytest.raises(FrameError) as excinfo:
                widen_frame_table(table)
            assert excinfo.value.category == "schema_violation"


class TestDictionaryEncodedNestedUnitRequest:
    def test_builds_as_the_decoded_column_does(self) -> None:
        requests = pa.array(
            [
                {"currency": "USD", "prices": "constant", "base_year": 2020},
                {"currency": "EUR", "prices": "current", "base_year": None},
            ],
            _UNIT_REQUEST_TYPE,
        )
        indices = pa.array([0, 1, 0], pa.int32())
        encoded = pa.DictionaryArray.from_arrays(indices, requests)
        refs = pa.nulls(3, pa.string())

        def unit_column(request: pa.Array) -> pa.StructArray:
            return pa.StructArray.from_arrays([refs, request], names=["ref", "request"])

        def table(unit: pa.Array) -> pa.Table:
            return pa.table(
                {
                    "stat_var": ["dcid:X"] * 3,
                    "provider": ["country/A", "country/B", "country/C"],
                    "time_period": ["2015"] * 3,
                    "value": [1.0, 2.0, 3.0],
                    "unit": unit,
                }
            )

        decoded = requests.take(indices)
        got = build_frame_from_arrow(
            table(unit_column(encoded)), role_keys=["provider"]
        )
        expected = build_frame_from_arrow(
            table(unit_column(decoded)), role_keys=["provider"]
        )
        assert got == expected
        assert unit_codes(unit_column(encoded)).codes.to_pylist() == [0, 1, 0]


class TestBuilderNormalisesEachDistinctUnitOnce:
    def test_unit_request_normaliser_runs_per_distinct_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requests = [
            {"currency": "USD", "prices": "constant", "base_year": 2020},
            {"currency": "EUR", "prices": "current", "base_year": None},
            None,
        ]
        table = pa.table(
            {
                "stat_var": ["dcid:X"] * 300,
                "provider": [f"country/{i:03d}" for i in range(300)],
                "time_period": ["2015"] * 300,
                "value": [1.0] * 300,
                "unit": pa.array(
                    [requests[i % 3] for i in range(300)], _UNIT_REQUEST_TYPE
                ),
            }
        )
        calls = 0
        original = arrow_module._unit_request_from_mapping

        def counting(raw: object) -> grammar.UnitRequest | None:
            nonlocal calls
            calls += 1
            return original(raw)

        monkeypatch.setattr(arrow_module, "_unit_request_from_mapping", counting)
        frame = build_frame_from_arrow(table, role_keys=["provider"])
        assert calls == 3
        monkeypatch.undo()
        rows = table.to_pylist()
        for row in rows:
            if row["unit"] is not None:
                row["unit"] = {k: v for k, v in row["unit"].items() if v is not None}
        assert frame == Frame.build(role_keys=["provider"], rows=rows)

    def test_first_invalid_row_is_the_one_refused(self) -> None:
        table = pa.table(
            {
                "stat_var": ["dcid:X"] * 3,
                "provider": ["country/A", "country/B", "country/C"],
                "time_period": ["2015"] * 3,
                "value": [1.0] * 3,
                "unit": pa.array(
                    [
                        {"currency": "USD", "prices": "current", "base_year": None},
                        {"currency": "usd", "prices": "current", "base_year": None},
                        {"currency": "USD", "prices": "bogus", "base_year": None},
                    ],
                    _UNIT_REQUEST_TYPE,
                ),
            }
        )
        with pytest.raises(FrameError, match="ISO 4217"):
            build_frame_from_arrow(table, role_keys=["provider"])


class TestCollationSortKeys:
    @pytest.mark.parametrize("seed", range(20))
    def test_sorting_by_the_keys_matches_row_sort_key(self, seed: int) -> None:
        rng = random.Random(seed)
        frame = _mixed_unit_frame()
        table = frame.table.take(
            pa.array(rng.sample(range(frame.num_rows), frame.num_rows))
        )
        grain = [
            column
            for column in frame.columns
            if column not in (grammar.VALUE_COLUMN, grammar.STATUS_COLUMN)
        ]
        keys = collation_sort_keys(table, grain)
        indices = array_kernel(
            "sort_indices",
            keys,
            sort_keys=[(name, "ascending") for name in keys.column_names],
        )
        shuffled = Frame.from_table(columns=frame.columns, table=table)
        expected = sorted(
            range(table.num_rows),
            key=lambda i: grammar.row_sort_key(frame.columns, shuffled.rows[i]),
        )
        assert indices.to_pylist() == expected

    def test_string_nulls_sort_first_and_by_code_point(self) -> None:
        values = pa.array(["é", None, "z", "", "e"])
        rank, payload = collation_keys(values)
        table = pa.table({"rank": rank, "payload": payload})
        order = array_kernel(
            "sort_indices",
            table,
            sort_keys=[("rank", "ascending"), ("payload", "ascending")],
        )
        cells = values.to_pylist()
        expected = sorted(
            range(len(cells)), key=lambda i: grammar.cell_sort_key(cells[i])
        )
        assert order.to_pylist() == expected

    def test_unit_forms_rank_null_ref_request_composed(self) -> None:
        cells = [
            grammar.ComposedUnit("USDollar", grammar.StatVarPart("dcid:Count_Person")),
            grammar.UnitRequest("USD", "current"),
            "USDollar",
            None,
        ]
        unit = pa.concat_arrays([unit_cell_array(cell, 1) for cell in cells])
        rank, payload = collation_keys(unit)
        assert rank.to_pylist() == [3, 2, 1, 0]
        assert payload.to_pylist() == [
            b'{"denominator":{"stat_var":"dcid:Count_Person"},"numerator":"USDollar"}',
            b'{"currency":"USD","prices":"current"}',
            b"USDollar",
            b"",
        ]

    def test_no_columns_keeps_the_row_count(self) -> None:
        frame = _mixed_unit_frame()
        keys = collation_sort_keys(frame.table, [])
        assert keys.num_rows == frame.num_rows
        assert keys.num_columns == 0

    def test_refuses_a_type_without_a_grammar_collation(self) -> None:
        with pytest.raises(TypeError):
            collation_keys(pa.array([1.0]))
