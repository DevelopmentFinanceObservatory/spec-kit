"""Compare fresh-process peak RSS for the Arrow and compatibility builders,
and for hashing a built frame with `frame_sha256` (streamed) against one
whole-table JCS document, the form `canonical_frame_bytes` took before it
streamed."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import statistics
import subprocess
import sys

import pyarrow as pa
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import grammar
from dfo_frame_grammar.arrow import build_frame_from_arrow
from dfo_frame_grammar.content_version import frame_sha256
from dfo_frame_grammar.frame import Frame, _column_cells

ROW_COUNT = 250_000
CHUNK_COUNT = 44
RUNS = 3
MODES = (
    "compatibility",
    "arrow",
    "arrow_rows",
    "arrow_frame_sha256",
    "arrow_whole_table_sha256",
)


def _table() -> pa.Table:
    """Build a deterministic, nullable-string table split across 44 chunks."""
    rows_per_chunk, remainder = divmod(ROW_COUNT, CHUNK_COUNT)
    chunks: dict[str, list[pa.Array]] = {
        "stat_var": [],
        "provider": [],
        "time_period": [],
        "value": [],
        "unit": [],
    }
    offset = 0
    for chunk in range(CHUNK_COUNT):
        count = rows_per_chunk + (chunk < remainder)
        indices = range(offset, offset + count)
        chunks["stat_var"].append(pa.array([f"dcid:StatVar_{i % 97}" for i in indices]))
        chunks["provider"].append(pa.array([f"country/{i:06d}" for i in indices]))
        chunks["time_period"].append(pa.array([str(1990 + i % 35) for i in indices]))
        chunks["value"].append(pa.array([float(i % 10_000) for i in indices]))
        chunks["unit"].append(pa.array(["USD" if i % 3 else None for i in indices]))
        offset += count
    return pa.table({name: pa.chunked_array(values) for name, values in chunks.items()})


def _rss_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _whole_table_sha256(frame: Frame) -> str:
    rows = [
        list(row)
        for row in zip(
            *_column_cells(frame.columns, frame.table, json_form=True), strict=True
        )
    ]
    document = {
        "frame_version": grammar.FRAME_VERSION,
        "columns": list(frame.columns),
        "rows": rows,
    }
    return hashlib.sha256(canonicalize(document)).hexdigest()


def _worker(mode: str) -> None:
    table = _table()
    if mode == "compatibility":
        Frame.build(role_keys=["provider"], rows=table.to_pylist())
    else:
        frame = build_frame_from_arrow(table, role_keys=["provider"])
        if mode == "arrow_rows":
            _ = frame.rows
        elif mode == "arrow_frame_sha256":
            frame_sha256(frame)
        elif mode == "arrow_whole_table_sha256":
            _whole_table_sha256(frame)
    print(json.dumps({"mode": mode, "peak_rss_mib": _rss_mib()}))


def _run(mode: str) -> list[float]:
    command = [sys.executable, __file__, "--worker", mode]
    result: list[float] = []
    for _ in range(RUNS):
        completed = subprocess.run(command, capture_output=True, text=True, check=True)
        result.append(json.loads(completed.stdout)["peak_rss_mib"])
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=MODES)
    args = parser.parse_args()
    if args.worker:
        _worker(args.worker)
        return
    results = {mode: _run(mode) for mode in MODES}
    medians = {mode: statistics.median(values) for mode, values in results.items()}
    print(json.dumps({"runs": results, "median_peak_rss_mib": medians}, indent=2))
    if medians["arrow"] >= medians["compatibility"]:
        raise SystemExit("direct Arrow peak RSS was not below the compatibility path")
    if medians["arrow_frame_sha256"] >= medians["arrow_whole_table_sha256"]:
        raise SystemExit("streamed frame_sha256 peak RSS was not below the whole table")


if __name__ == "__main__":
    main()
