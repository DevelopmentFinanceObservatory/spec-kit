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
from dfo_frame_grammar.frame import Frame, canonical_frame_schema
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
        assert frame.num_rows == 18
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
        assert codes.count == 6
        cells = unit_cells(unit, codes)
        rows = [row[frame.columns.index(grammar.UNIT_COLUMN)] for row in frame.rows]
        assert [cells[code] for code in codes.codes.to_pylist()] == rows

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

    def test_no_columns_keeps_the_row_count(self) -> None:
        frame = _mixed_unit_frame()
        keys = collation_sort_keys(frame.table, [])
        assert keys.num_rows == frame.num_rows
        assert keys.num_columns == 0

    def test_refuses_a_type_without_a_grammar_collation(self) -> None:
        with pytest.raises(TypeError):
            collation_keys(pa.array([1.0]))
