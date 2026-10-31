"""`stream_canonical_frame_bytes` and `stream_frame_sha256` -- the streaming
forms of the canonical frame document over an iterable of validated `Frame`
chunks (see `dfo_frame_grammar.frame` for the byte-level argument for why
this works: a JCS document's `rows` array serialises each element
independently, and this grammar's own sorted top-level keys put `rows`
last). `canonical_frame_bytes` and `frame_sha256` are the streaming forms
over one chunk.

Every equivalence claim here is checked against `_whole_table_canonical_bytes`,
which serialises the whole table as one JCS document the way
`canonical_frame_bytes` did before it streamed, never against the streaming
code's own output.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import itertools
import json
import random
import unicodedata

import pyarrow as pa
import pytest
from dfo_spec_kit.identity import canonicalize

from dfo_frame_grammar import frame as frame_module
from dfo_frame_grammar import grammar
from dfo_frame_grammar.arrow import build_frame_from_arrow
from dfo_frame_grammar.content_version import frame_sha256, stream_frame_sha256
from dfo_frame_grammar.frame import (
    Frame,
    canonical_frame_bytes,
    stream_canonical_frame_bytes,
)
from dfo_frame_grammar.grammar import FrameError

_ROLE_KEYS = ("provider", "recipient")


def _whole_table_canonical_bytes(frame: Frame) -> bytes:
    """The oracle: the whole table read at once and serialised as one JCS
    document."""
    rows = [
        list(row)
        for row in zip(
            *frame_module._column_cells(frame.columns, frame.table, json_form=True),
            strict=True,
        )
    ]
    return canonicalize(
        {
            "frame_version": grammar.FRAME_VERSION,
            "columns": list(frame.columns),
            "rows": rows,
        }
    )


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


def _load_positive_frame_vectors() -> list[dict]:
    """Every "frame"-kind, positive-classification vector this package
    ships -- the same package-data suite `test_vectors.py` runs, reloaded
    here rather than imported from that module so this file's own vector
    selection stays independent of that module's private helpers.
    """
    root = importlib.resources.files("dfo_frame_grammar") / "vectors" / "frame"
    vectors: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".json") or entry.name == "frame-vector.schema.json":
            continue
        with entry.open("rb") as f:
            vector = json.load(f)
        if vector["kind"] == "frame" and vector["classification"] == "positive":
            vectors.append(vector)
    return vectors


_POSITIVE_FRAME_VECTORS = _load_positive_frame_vectors()


def _chunk_frame(frame: Frame, boundaries: list[int]) -> list[Frame]:
    """Split `frame`'s own canonical table into contiguous slices at each
    row offset in `boundaries`, one `Frame` per slice, including any
    zero-length slice a repeated or edge boundary produces.

    Slicing the already-built, already-sorted table -- rather than
    re-running each slice's rows back through `Frame.build` -- is what
    keeps a chunk's `unit` cells in their post-normalisation form: a
    `grammar.UnitRequest` cell `Frame.build` produced is valid inside a
    `Frame.table`, but is not itself a valid `Frame.build` row input (that
    function's `unit` domain is a bare string, a plain mapping, or
    `None`). This is also exactly how a real caller assembles chunks from
    one already-built frame, never by re-validating raw rows a second
    time.
    """
    total = frame.table.num_rows
    offsets = sorted({0, *boundaries, total})
    if len(offsets) == 1:
        # `total == 0` and no boundary distinct from it: still one chunk,
        # a zero-length slice, so a zero-row frame always streams as at
        # least one (empty) chunk rather than none.
        offsets = [offsets[0], offsets[0]]
    return [
        Frame(columns=frame.columns, table=frame.table.slice(start, end - start))
        for start, end in itertools.pairwise(offsets)
    ]


def _assert_streams_match(frame: Frame, chunks: list[Frame]) -> None:
    expected_bytes = _whole_table_canonical_bytes(frame)
    assert canonical_frame_bytes(frame) == expected_bytes
    streamed_bytes = b"".join(stream_canonical_frame_bytes(chunks))
    assert streamed_bytes == expected_bytes

    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    assert frame_sha256(frame) == expected_sha
    assert stream_frame_sha256(chunks) == expected_sha


class TestGoldenVectorEquivalence:
    """Every existing positive frame vector, streamed back at three
    granularities: as one whole chunk, split down the middle, and split
    into one chunk per row (the finest granularity a real caller could
    choose)."""

    def test_suite_is_non_empty(self) -> None:
        assert len(_POSITIVE_FRAME_VECTORS) > 0

    @pytest.mark.parametrize(
        "vector",
        _POSITIVE_FRAME_VECTORS,
        ids=[v["name"] for v in _POSITIVE_FRAME_VECTORS],
    )
    def test_single_chunk_matches(self, vector: dict) -> None:
        frame = Frame.build(role_keys=vector["role_keys"], rows=vector["rows"])
        _assert_streams_match(frame, [frame])

    @pytest.mark.parametrize(
        "vector",
        _POSITIVE_FRAME_VECTORS,
        ids=[v["name"] for v in _POSITIVE_FRAME_VECTORS],
    )
    def test_split_at_the_midpoint_matches(self, vector: dict) -> None:
        frame = Frame.build(role_keys=vector["role_keys"], rows=vector["rows"])
        midpoint = frame.table.num_rows // 2
        _assert_streams_match(frame, _chunk_frame(frame, [midpoint]))

    @pytest.mark.parametrize(
        "vector",
        _POSITIVE_FRAME_VECTORS,
        ids=[v["name"] for v in _POSITIVE_FRAME_VECTORS],
    )
    def test_one_row_per_chunk_matches(self, vector: dict) -> None:
        frame = Frame.build(role_keys=vector["role_keys"], rows=vector["rows"])
        boundaries = list(range(1, frame.table.num_rows))
        _assert_streams_match(frame, _chunk_frame(frame, boundaries))


class TestEdgeCaseCellsAcrossChunkBoundaries:
    """A multi-row frame combining the cell shapes the streaming form's
    per-row JSON conversion and row-sort-key computation both have to
    handle: a unit-request object, a bare-string unit, 0 and -0.0
    (converging on the same cell), an integral float, and a non-ASCII role
    value -- streamed at several chunk boundaries, including a
    zero-length chunk at the start, in the middle, and at the end.
    """

    def _frame(self) -> Frame:
        rows = [
            _row(
                time_period="2010",
                value=-0.0,
                provider="país/ABC",  # non-ASCII role value
            ),
            _row(time_period="2011", value=0.0),
            _row(
                time_period="2012",
                value=250_000.0,  # integral float
                unit={"currency": "EUR", "prices": "constant", "base_year": 2023},
            ),
            _row(time_period="2013", value=1e21, unit="USD"),
            _row(time_period="2014", value=3.5, provenance="café"),
        ]
        return Frame.build(role_keys=_ROLE_KEYS, rows=rows)

    def test_single_chunk_matches(self) -> None:
        frame = self._frame()
        _assert_streams_match(frame, [frame])

    def test_one_row_per_chunk_matches(self) -> None:
        frame = self._frame()
        boundaries = list(range(1, frame.table.num_rows))
        _assert_streams_match(frame, _chunk_frame(frame, boundaries))

    def test_leading_middle_and_trailing_empty_chunks_are_transparent(self) -> None:
        frame = self._frame()
        total = frame.table.num_rows
        # Boundaries at 0 and at `total` each produce a zero-length slice
        # (at the start and at the end respectively); 2 repeated produces
        # one in the middle too.
        _assert_streams_match(frame, _chunk_frame(frame, [0, 2, 2, total]))


class TestChunksInBothUnitLayouts:
    """Chunks wrapped one row each by `Frame.from_table`, which gives a
    chunk the recipe layout only when one of its cells carries a recipe, so
    the stream alternates between the two layouts."""

    def test_streams_as_the_whole_frame(self) -> None:
        recipe = {"currency": "EUR", "prices": "current", "recipe": "recipe/eur"}
        frame = Frame.build(
            role_keys=_ROLE_KEYS,
            rows=[
                _row(time_period="2019", unit=recipe),
                _row(time_period="2020", unit="USD"),
                _row(time_period="2021", unit=recipe),
            ],
        )
        chunks = [
            Frame.from_table(columns=frame.columns, table=chunk.table)
            for chunk in _chunk_frame(frame, [1, 2])
        ]
        layouts = [chunk.table.schema.field("unit").type for chunk in chunks]
        assert layouts[0] == layouts[2] != layouts[1]
        _assert_streams_match(frame, chunks)


class TestEmptyFrameStreaming:
    def test_a_single_empty_chunk_matches_the_in_memory_empty_frame(self) -> None:
        frame = Frame.build(role_keys=_ROLE_KEYS, rows=[])
        _assert_streams_match(frame, [frame])

    def test_an_empty_leading_chunk_before_real_rows_matches(self) -> None:
        empty = Frame.build(role_keys=_ROLE_KEYS, rows=[])
        full = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(), _row(time_period="2016")]
        )
        _assert_streams_match(full, [empty, full])


def _single_role_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "stat_var": "dcid:MalariaGrantDisbursements",
        "provider": "country/FRA",
        "time_period": "2015",
        "value": 1000.0,
    }
    row.update(overrides)
    return row


class TestColumnMismatchRefusal:
    def test_a_later_chunk_with_different_columns_refuses(self) -> None:
        first = Frame.build(role_keys=("provider",), rows=[_single_role_row()])
        second = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="2016")])
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([first, second]))
        assert excinfo.value.category == "column_mismatch"

    def test_the_sha256_form_refuses_the_same_way(self) -> None:
        first = Frame.build(role_keys=("provider",), rows=[_single_role_row()])
        second = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="2016")])
        with pytest.raises(FrameError) as excinfo:
            stream_frame_sha256([first, second])
        assert excinfo.value.category == "column_mismatch"

    def test_a_disorder_before_the_mismatched_chunk_refuses_first(self) -> None:
        # The descending pair sits in rows still buffered when the
        # mismatched chunk arrives, so they must stream before it refuses.
        ordered = Frame.build(
            role_keys=_ROLE_KEYS,
            rows=[_row(time_period="2015"), _row(time_period="2016")],
        )
        out_of_order = Frame(
            columns=ordered.columns, table=ordered.table.take(pa.array([1, 0]))
        )
        other = Frame.build(role_keys=("provider",), rows=[_single_role_row()])
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([out_of_order, other]))
        assert excinfo.value.category == "row_key_not_increasing"

    def test_a_lone_empty_chunk_with_different_columns_still_sets_the_columns(
        self,
    ) -> None:
        # An empty chunk still carries real columns; a later chunk
        # disagreeing with *that* chunk's columns refuses even though the
        # first chunk contributed no rows.
        empty = Frame.build(role_keys=("provider",), rows=[])
        full = Frame.build(role_keys=_ROLE_KEYS, rows=[_row()])
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([empty, full]))
        assert excinfo.value.category == "column_mismatch"


class TestRowKeyOrderingRefusal:
    # A one-row slice flushes the buffer at every chunk, so the refusal
    # compares keys across a flush; the default slice holds both chunks.
    @pytest.mark.parametrize("slice_rows", [1, frame_module._STREAM_SLICE_ROWS])
    def test_descending_key_across_a_chunk_boundary_refuses(
        self, monkeypatch: pytest.MonkeyPatch, slice_rows: int
    ) -> None:
        monkeypatch.setattr(frame_module, "_STREAM_SLICE_ROWS", slice_rows)
        earlier = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="2016")])
        later = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="2015")])
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([earlier, later]))
        assert excinfo.value.category == "row_key_not_increasing"

    @pytest.mark.parametrize("slice_rows", [1, frame_module._STREAM_SLICE_ROWS])
    def test_equal_key_across_a_chunk_boundary_refuses(
        self, monkeypatch: pytest.MonkeyPatch, slice_rows: int
    ) -> None:
        monkeypatch.setattr(frame_module, "_STREAM_SLICE_ROWS", slice_rows)
        first = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="2015")])
        second = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(time_period="2015")])
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([first, second]))
        assert excinfo.value.category == "row_key_not_increasing"

    def test_descending_key_within_a_single_chunk_refuses(self) -> None:
        # `Frame.build` itself never produces an out-of-order table; this
        # simulates a `Frame` assembled by some other means (a
        # hand-constructed `Frame(columns=..., table=...)`, as this
        # package's own `Frame` dataclass permits) whose table rows are
        # not sorted.
        ordered = Frame.build(
            role_keys=_ROLE_KEYS,
            rows=[_row(time_period="2015"), _row(time_period="2016")],
        )
        reversed_table = ordered.table.take(pa.array([1, 0]))
        out_of_order = Frame(columns=ordered.columns, table=reversed_table)
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([out_of_order]))
        assert excinfo.value.category == "row_key_not_increasing"

    def test_equal_key_within_a_single_chunk_refuses(self) -> None:
        single = Frame.build(role_keys=_ROLE_KEYS, rows=[_row(value=1.0)])
        # Two rows sharing a grain, both surviving into one table only
        # because they were assembled by hand rather than through
        # `Frame.build` (which refuses this at construction, per
        # `TestDuplicateRowKeyRefusal` in test_frame.py).
        duplicated_table = pa.concat_tables([single.table, single.table])
        with_duplicate = Frame(columns=single.columns, table=duplicated_table)
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([with_duplicate]))
        assert excinfo.value.category == "row_key_not_increasing"


class TestSingleFrameOrderingRefusal:
    """`canonical_frame_bytes` and `frame_sha256` stream one chunk, so a
    table wrapped out of canonical order refuses there too."""

    def _reversed(self) -> Frame:
        ordered = Frame.build(
            role_keys=_ROLE_KEYS,
            rows=[_row(time_period="2015"), _row(time_period="2016")],
        )
        return Frame.from_table(
            columns=ordered.columns, table=ordered.table.take(pa.array([1, 0]))
        )

    def test_canonical_frame_bytes_refuses(self) -> None:
        with pytest.raises(FrameError) as excinfo:
            canonical_frame_bytes(self._reversed())
        assert excinfo.value.category == "row_key_not_increasing"

    def test_frame_sha256_refuses(self) -> None:
        with pytest.raises(FrameError) as excinfo:
            frame_sha256(self._reversed())
        assert excinfo.value.category == "row_key_not_increasing"


class TestEmptyChunkIterableRefusal:
    def test_zero_chunks_refuses(self) -> None:
        with pytest.raises(FrameError) as excinfo:
            b"".join(stream_canonical_frame_bytes([]))
        assert excinfo.value.category == "schema_violation"

    def test_the_sha256_form_refuses_the_same_way(self) -> None:
        with pytest.raises(FrameError) as excinfo:
            stream_frame_sha256([])
        assert excinfo.value.category == "schema_violation"


class TestStreamingNeverAccessesTheCompatibilityRowsView:
    def test_stream_canonical_frame_bytes_does_not_read_frame_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frame = Frame.build(
            role_keys=_ROLE_KEYS, rows=[_row(), _row(time_period="2016")]
        )

        def fail(_self: Frame) -> tuple[tuple[object, ...], ...]:
            raise AssertionError("streaming serialisation accessed Frame.rows")

        monkeypatch.setattr(Frame, "rows", property(fail))
        expected = _whole_table_canonical_bytes(frame)
        assert b"".join(stream_canonical_frame_bytes([frame])) == expected
        assert canonical_frame_bytes(frame) == expected
        assert frame_sha256(frame) == hashlib.sha256(expected).hexdigest()


class TestStreamSliceSizeIsTransparent:
    """`stream_canonical_frame_bytes` reads one chunk's table in bounded
    `frame._STREAM_SLICE_ROWS`-row slices rather than all at once (see that
    module's comment on the constant). The slice size is an internal memory
    bound, not part of the emitted bytes: it must produce byte-identical
    output regardless of its value, including sizes that split a chunk's 11
    rows unevenly (1, 3) and the shipped default (8192, which never splits
    this small a chunk at all). Slices span chunk boundaries, so the rows
    also arrive as one chunk and as chunks of two, which a three-row slice
    straddles.
    """

    def _frame(self) -> Frame:
        rows = [_row(time_period=str(2010 + i), value=float(i)) for i in range(11)]
        return Frame.build(role_keys=_ROLE_KEYS, rows=rows)

    @pytest.mark.parametrize("boundaries", [[], [2, 4, 6, 8, 10]])
    @pytest.mark.parametrize("slice_rows", [1, 3, frame_module._STREAM_SLICE_ROWS])
    def test_bytes_are_identical_across_slice_sizes(
        self, monkeypatch: pytest.MonkeyPatch, slice_rows: int, boundaries: list[int]
    ) -> None:
        built_frame = self._frame()
        monkeypatch.setattr(frame_module, "_STREAM_SLICE_ROWS", slice_rows)
        chunks = _chunk_frame(built_frame, boundaries)
        streamed_bytes = b"".join(stream_canonical_frame_bytes(chunks))
        assert streamed_bytes == _whole_table_canonical_bytes(built_frame)


_ALPHABET = ["a", "b", "Z", "é", "e\u0301", "å", "ß", "\U0001f600"]


def _random_ref(rng: random.Random, prefix: str) -> str:
    return prefix + "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 4)))


def _random_unit_part(rng: random.Random) -> object:
    choice = rng.randrange(4)
    if choice == 0:
        return {"stat_var": _random_ref(rng, "dcid:")}
    if choice == 1:
        return _random_ref(rng, "unit/")
    currency = rng.choice(["USD", "EUR", "GBP"])
    if choice == 2:
        return {"currency": currency, "prices": "current"}
    return {
        "currency": currency,
        "prices": "constant",
        "base_year": rng.choice([2, 10, 2015, 2021]),
    }


def _random_unit(rng: random.Random) -> object:
    choice = rng.randrange(5)
    if choice == 0:
        return None
    if choice == 1:
        return {
            "numerator": _random_unit_part(rng),
            "denominator": _random_unit_part(rng),
        }
    part = _random_unit_part(rng)
    while isinstance(part, dict) and "stat_var" in part:
        part = _random_unit_part(rng)
    return part


def _random_rows(rng: random.Random, role_keys: list[str]) -> list[dict[str, object]]:
    rows: dict[tuple, dict[str, object]] = {}
    for _ in range(rng.randint(0, 60)):
        row: dict[str, object] = {
            "stat_var": _random_ref(rng, "dcid:"),
            "time_period": str(rng.randint(2000, 2004)),
            "value": rng.choice(
                [-0.0, 0.0, 1e16, 0.1, rng.uniform(-1e9, 1e9), rng.randint(-9, 9)]
            ),
            "unit": _random_unit(rng),
        }
        for role in role_keys:
            row[role] = _random_ref(rng, "country/")
        for axis in grammar.FACET_IDENTITY_COLUMNS:
            if rng.random() < 0.5:
                row[axis] = _random_ref(rng, f"{axis}/")
        grain = tuple(
            unicodedata.normalize(
                "NFC", json.dumps(row.get(column), sort_keys=True, ensure_ascii=False)
            )
            for column in (
                "stat_var",
                "time_period",
                "unit",
                *role_keys,
                *grammar.FACET_IDENTITY_COLUMNS,
            )
        )
        rows[grain] = row
    return list(rows.values())


class TestCanonicalBytesMatchTheWholeTableOracle:
    """Seeded random frames, with unit refs, requests and composed units,
    sparse facets, decomposed and astral characters, and signed zeros,
    serialised through the streaming path at several slice sizes and
    compared with the oracle.
    """

    @pytest.mark.parametrize("slice_rows", [1, 7, frame_module._STREAM_SLICE_ROWS])
    @pytest.mark.parametrize("seed", range(40))
    def test_streamed_bytes_equal_the_oracle(
        self, monkeypatch: pytest.MonkeyPatch, seed: int, slice_rows: int
    ) -> None:
        rng = random.Random(seed)
        role_keys = rng.sample(["provider", "recipient", "état"], k=rng.randint(1, 3))
        frame = Frame.build(role_keys=role_keys, rows=_random_rows(rng, role_keys))
        monkeypatch.setattr(frame_module, "_STREAM_SLICE_ROWS", slice_rows)

        expected = _whole_table_canonical_bytes(frame)
        assert canonical_frame_bytes(frame) == expected
        assert frame_sha256(frame) == hashlib.sha256(expected).hexdigest()

        source = frame.table.drop_columns([grammar.STATUS_COLUMN])
        from_arrow = build_frame_from_arrow(source, role_keys=role_keys)
        assert canonical_frame_bytes(from_arrow) == expected
