"""Report the size of the canonical `unit` column and the time to read and
hash a frame whose rows carry composed units, and the time to build a frame
from rows that share one composed unit.

The script touches only the package's public API, so the same file runs on
revisions that lay the `composed` member out differently. Composed units are
built from their JSON form with `unit_cell_from_json` and `unit_cell_array`.

Bytes per row are `nbytes / rows` of the `unit` column: the two-member
layout `struct<ref, request>` and the canonical layout over the same ref
rows, both built with `pa.array` as `TestUnitColumnBytes` builds them, and
the frame column over composed rows cycling five distinct units and over
composed rows that are all distinct. Times are the median of three runs of
each public call over the two composed frames, of `Frame.build` over rows
that share one composed unit, and of `stream_canonical_frame_bytes` over a
frame of mixed ref, request and composed units and over a frame of unit
requests, each cut into small chunks.

Runtime is about a minute per revision.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

import pyarrow as pa

from dfo_frame_grammar import (
    Frame,
    build_frame_from_arrow,
    canonical_frame_schema,
    collation_keys,
    derive_columns,
    frame_sha256,
    stream_canonical_frame_bytes,
    unit_cell_array,
    unit_cell_from_json,
    unit_cells,
    unit_codes,
)

ROW_COUNT = 250_000
BUILD_ROW_COUNT = 100_000
STREAM_ROW_COUNT = 50_000
STREAM_CHUNK_ROWS = (1, 32, 100)
STREAM_UNITS: dict[str, list[object]] = {
    "mixed units": [
        "USD",
        {"currency": "USD", "prices": "constant", "base_year": 2020},
        {"numerator": "USD", "denominator": {"stat_var": "dcid:Count_Person"}},
    ],
    "unit requests": [
        {"currency": "USD", "prices": "constant", "base_year": year}
        for year in range(2000, 2025)
    ],
}
CYCLED_UNITS = 5
RUNS = 3
ROLE_KEYS = ["provider"]


def _composed_array(distinct: int) -> pa.Array:
    """`ROW_COUNT` canonical `unit` rows cycling `distinct` composed units."""
    pieces = [
        unit_cell_array(
            unit_cell_from_json(
                {
                    "numerator": "USD",
                    "denominator": {"stat_var": f"dcid:StatVar_{i}"},
                }
            ),
            1,
        )
        for i in range(distinct)
    ]
    indices = pa.array([i % distinct for i in range(ROW_COUNT)], type=pa.int32())
    return pa.concat_arrays(pieces).take(indices)


def _table(unit: pa.Array) -> pa.Table:
    """A frame input over `unit` with one distinct provider per row."""
    return pa.table(
        {
            "stat_var": pa.array(["dcid:StatVar_0"] * ROW_COUNT),
            "provider": pa.array([f"country/{i:06d}" for i in range(ROW_COUNT)]),
            "time_period": pa.array(["2020"] * ROW_COUNT),
            "value": pa.array([float(i % 10_000) for i in range(ROW_COUNT)]),
            "unit": unit,
        }
    )


def _unit_type() -> pa.StructType:
    columns = derive_columns(ROLE_KEYS)
    return canonical_frame_schema(columns).field("unit").type


def _two_member_array() -> pa.Array:
    unit_type = _unit_type()
    two_member = pa.struct([unit_type.field("ref"), unit_type.field("request")])
    refs = [{"ref": "USD", "request": None}] * ROW_COUNT
    return pa.array(refs, type=two_member)


def _canonical_ref_array() -> pa.Array:
    refs = [{"ref": "USD"}] * ROW_COUNT
    return pa.array(refs, type=_unit_type())


def _median_seconds[T](call: Callable[[], T]) -> tuple[float, T]:
    """The median wall time of `RUNS` calls and the last call's result."""
    times: list[float] = []
    for _ in range(RUNS):
        start = time.perf_counter()
        result = call()
        times.append(time.perf_counter() - start)
    return statistics.median(times), result


def _bytes_per_row(array: pa.Array | pa.ChunkedArray) -> float:
    return array.nbytes / len(array)


def _build_seconds() -> float:
    """The median `Frame.build` time over `BUILD_ROW_COUNT` rows that share
    one composed unit."""
    unit = {
        "numerator": "USD",
        "denominator": {"stat_var": "dcid:StatVar_0"},
    }
    rows = [
        {
            "stat_var": "dcid:StatVar_0",
            "provider": f"country/{i:06d}",
            "time_period": "2020",
            "value": float(i % 10_000),
            "unit": unit,
        }
        for i in range(BUILD_ROW_COUNT)
    ]
    seconds, _ = _median_seconds(lambda: Frame.build(role_keys=ROLE_KEYS, rows=rows))
    return seconds


def _stream_frame(units: list[object]) -> Frame:
    """A `STREAM_ROW_COUNT`-row frame whose rows cycle through `units`."""
    rows = [
        {
            "stat_var": "dcid:StatVar_0",
            "provider": f"country/{i:06d}",
            "time_period": "2020",
            "value": 1.0,
            "unit": units[i % len(units)],
        }
        for i in range(STREAM_ROW_COUNT)
    ]
    return Frame.build(role_keys=ROLE_KEYS, rows=rows)


def _stream_seconds(frame: Frame, chunk_rows: int) -> float:
    """The median time to stream the bytes of `frame` in chunks of
    `chunk_rows` rows."""
    chunks = [
        Frame.from_table(
            columns=frame.columns, table=frame.table.slice(start, chunk_rows)
        )
        for start in range(0, frame.num_rows, chunk_rows)
    ]
    seconds, _ = _median_seconds(lambda: b"".join(stream_canonical_frame_bytes(chunks)))
    return seconds


def _frame_timings(unit: pa.Array) -> tuple[dict[str, float], pa.ChunkedArray]:
    table = _table(unit)
    timings: dict[str, float] = {}
    timings["build_frame_from_arrow"], frame = _median_seconds(
        lambda: build_frame_from_arrow(table, role_keys=ROLE_KEYS)
    )
    column = frame.table.column("unit")
    timings["unit_codes"], codes = _median_seconds(lambda: unit_codes(column))
    timings["unit_cells"], _ = _median_seconds(lambda: unit_cells(column, codes))
    timings["collation_keys"], _ = _median_seconds(lambda: collation_keys(column))
    timings["frame_sha256"], _ = _median_seconds(lambda: frame_sha256(frame))
    timings["Frame.rows"], _ = _median_seconds(lambda: frame.rows)
    return timings, column


def main() -> None:
    cycled_timings, cycled_column = _frame_timings(_composed_array(CYCLED_UNITS))
    distinct_timings, distinct_column = _frame_timings(_composed_array(ROW_COUNT))

    print(f"rows: {ROW_COUNT:,}")
    print()
    print("unit column bytes per row")
    sizes = {
        "two-member, pa.array": _bytes_per_row(_two_member_array()),
        "canonical, refs, pa.array": _bytes_per_row(_canonical_ref_array()),
        f"canonical, composed x{CYCLED_UNITS} distinct": _bytes_per_row(cycled_column),
        "canonical, composed all distinct": _bytes_per_row(distinct_column),
    }
    for name, size in sizes.items():
        print(f"  {name:<36}{size:>8.1f}")

    print()
    print(f"median seconds over {RUNS} runs, composed frames")
    print(f"  {'':<26}{f'x{CYCLED_UNITS} distinct':>14}{'all distinct':>14}")
    for name, cycled in cycled_timings.items():
        print(f"  {name:<26}{cycled:>14.3f}{distinct_timings[name]:>14.3f}")

    print()
    print(
        f"Frame.build, {BUILD_ROW_COUNT:,} rows sharing one composed unit, "
        f"median seconds: {_build_seconds():.3f}"
    )

    print()
    print(
        f"stream_canonical_frame_bytes, {STREAM_ROW_COUNT:,} rows in chunks, "
        f"median seconds over {RUNS} runs"
    )
    print(f"  {'chunk rows':<16}" + "".join(f"{name:>16}" for name in STREAM_UNITS))
    frames = [_stream_frame(units) for units in STREAM_UNITS.values()]
    for chunk_rows in STREAM_CHUNK_ROWS:
        seconds = [_stream_seconds(frame, chunk_rows) for frame in frames]
        print(f"  {chunk_rows:<16}" + "".join(f"{value:>16.3f}" for value in seconds))


if __name__ == "__main__":
    main()
