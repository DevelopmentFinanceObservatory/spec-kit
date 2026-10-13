"""Write `frame.parquet` and `frame.json`, a frame with no recipe in any
cell as the installed `dfo_frame_grammar` stores it.

The committed files were written by kit 959f8bc, the last revision before
the `recipe` member, whose `unit` column has no `recipe` child.
`tests/test_stored_frame.py` builds the same rows with the current package
and checks that it writes the same Parquet bytes, reads the stored file and
mints the same `content_version`.

The script uses only the public API both revisions share, so it runs against
either. Regenerate the files from a checkout of 959f8bc, under the
`pyarrow` version `uv.lock` pins, since a Parquet file records the writing
library's version:

    cd <checkout at 959f8bc>/python/frame-grammar
    uv run --frozen python <this file> <this directory>
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import dfo_frame_grammar
from dfo_frame_grammar import Frame
from dfo_frame_grammar.content_version import frame_sha256, mint_content_version

ROLE_KEYS = ("provider", "recipient")

# Every `unit` form a frame with no recipe holds: null, a ref, a unit request
# at current and at constant prices, and a composed unit with a request side,
# beside both facet-identity columns.
ROWS = (
    {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/FRA",
        "recipient": "country/TGO",
        "time_period": "2019",
        "value": 1000.0,
    },
    {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/FRA",
        "recipient": "country/TGO",
        "time_period": "2020",
        "value": 1250.5,
        "unit": "USDollar",
        "measurementMethod": "OECD_DAC_Disbursement",
        "provenance": "dc/base/OECD_CRS",
    },
    {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/FRA",
        "recipient": "country/TGO",
        "time_period": "2021",
        "value": 980.0,
        "unit": {"currency": "EUR", "prices": "current"},
    },
    {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/DEU",
        "recipient": "country/TGO",
        "time_period": "2021",
        "value": 2500.0,
        "unit": {"currency": "EUR", "prices": "constant", "base_year": 2023},
    },
    {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/DEU",
        "recipient": "country/TGO",
        "time_period": "2022",
        "value": 0.25,
        "unit": {
            "numerator": {"currency": "USD", "prices": "current"},
            "denominator": {"stat_var": "dcid:Count_Person"},
        },
    },
)


def build_frame() -> Frame:
    """The frame `ROWS` build."""
    return Frame.build(role_keys=ROLE_KEYS, rows=ROWS)


def parquet_bytes(frame: Frame) -> bytes:
    """`frame.table` written as one Parquet file under its own schema, as a
    service streams a stored frame through `pq.ParquetWriter`."""
    sink = io.BytesIO()
    with pq.ParquetWriter(sink, frame.table.schema) as writer:
        writer.write_table(frame.table)
    return sink.getvalue()


def main(out_dir: Path) -> None:
    """Write the frame's Parquet bytes and a record of the revision, the
    `pyarrow` version and the frame's hashes into `out_dir`."""
    frame = build_frame()
    package_dir = Path(dfo_frame_grammar.__file__).parent
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=package_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    digest = frame_sha256(frame)
    record = {
        "kit_revision": revision,
        "pyarrow_version": pa.__version__,
        "frame_sha256": digest,
        "content_version": mint_content_version(frame_sha256=digest),
    }
    (out_dir / "frame.parquet").write_bytes(parquet_bytes(frame))
    (out_dir / "frame.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
