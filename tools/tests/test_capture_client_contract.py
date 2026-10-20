"""Tests for tools/capture_client_contract.py.

The recorder's client reproduces each packaged record from the record's own
steps and responses, every derived record follows from its parent, and the
writers keep the kit's JSON formatting. No test reaches the network or needs
pyarrow: a fake transport serves the recorded responses, and the Parquet shape
reader is replaced, so rows and columns are the one part of a dataset outcome
these tests leave to the recording.

Run with: uv run --with jsonschema --with pytest pytest tools/tests -q
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "capture_client_contract.py"
_spec = importlib.util.spec_from_file_location("capture_client_contract", _MODULE_PATH)
capture = importlib.util.module_from_spec(_spec)
sys.modules["capture_client_contract"] = capture
_spec.loader.exec_module(capture)

SCENARIOS = capture.PACKAGE_DIR / "scenarios"
ACTIONS = capture.load_actions(capture.PACKAGE_DIR)


def _load(name: str) -> dict[str, Any]:
    return json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))


def _records(kind: str) -> list[str]:
    return sorted(
        p.stem
        for p in SCENARIOS.glob("*.json")
        if json.loads(p.read_text(encoding="utf-8"))["origin"]["kind"] == kind
    )


class _Served:
    """A transport serving one record's responses, and failing on any request
    the record does not hold in that order."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.base_url = record["origin"].get("base_url", capture.DEV_BASE_URL)
        self.pending = copy.deepcopy(record["exchanges"])

    def __call__(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Any:
        exchange = self.pending.pop(0)
        request, response = exchange["request"], exchange["response"]
        assert method == request["method"]
        if "url" in request:
            assert headers == {}, "a signed download carries no client header"
            assert capture.redact_external_url(url) == request["url"]
        elif "origin_path" in request:
            assert url == capture._origin(self.base_url) + request["origin_path"]
        else:
            assert url == self.base_url + request["path"]
        assert (json.loads(body) if body else None) == request.get("json")
        if "json" in response:
            data = json.dumps(response["json"]).encode()
        elif "body_file" in response:
            data = (SCENARIOS / response["body_file"]).read_bytes()
        else:
            data = b""
        return capture.Response(
            response["status"], dict(response.get("headers", {})), data
        )


def _shape(data: bytes) -> tuple[int, list[str]]:
    return len(data), ["column"]


def _without_shapes(expect: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expect = copy.deepcopy(expect)
    for outcome in expect:
        if "dataset" in outcome:
            dataset = outcome["dataset"]
            del dataset["rows"], dataset["columns"]
            for entry in dataset.get("part_rows", []):
                del entry["rows"]
    return expect


def _drive(session: Any, call: list[dict[str, Any]]) -> None:
    """Runs a record's steps through the recorder's client."""
    answer = None
    for step in call:
        match step:
            case {"op": "search", "question": question}:
                answer = session.search(question)
            case {"op": "choose", "option": option}:
                answer = session.choose(answer, option, part=step.get("part"))
            case {"op": "fetch", "spec": spec}:
                window = step.get("window")
                session.fetch_spec(
                    capture.Spec(
                        spec["spec_id"], spec["definition"], spec.get("sentence")
                    ),
                    window=tuple(window) if isinstance(window, list) else window,
                    facets=step.get("facets"),
                )
            case {"op": "fetch", "ref": ref}:
                session.fetch_ref(ref)
            case {"op": "fetch"}:
                session.fetch_answer(
                    answer,
                    partial=step.get("partial", False),
                    strict=step.get("strict", False),
                )
            case {"op": "mint", "spec": spec}:
                session.mint(
                    capture.Spec(
                        spec["spec_id"], spec["definition"], spec.get("sentence")
                    )
                )
            case {"op": "result"}:
                session.result()


_LIVE = sorted(d.name for d in capture.SYNTHETIC if d.live)


def test_every_synthetic_record_has_a_definition() -> None:
    assert sorted(d.name for d in capture.SYNTHETIC) == _records("synthetic")


@pytest.mark.parametrize("name", [*_records("recorded"), *_LIVE])
def test_the_recorder_reproduces_each_record_from_its_steps(name: str) -> None:
    record = _load(name)
    served = _Served(record)
    recorder = capture.Recorder(name, served.base_url, served)
    session = capture.Session(
        recorder, ACTIONS, read_shape=_shape, sleep=lambda _: None
    )
    _drive(session, record["call"])
    assert served.pending == [], "the client skipped a recorded exchange"
    assert session.call == record["call"]
    assert recorder.exchanges == record["exchanges"]
    assert _without_shapes(session.expect) == _without_shapes(record["expect"])


def test_a_recorder_keeps_bodies_redacts_signed_urls_and_names_the_commit() -> None:
    signed = "https://storage.example/bucket/sha256%3Aab?X-Goog-Algorithm=GOOG4&X-Goog-Signature=f00"
    replies = [
        capture.Response(
            302, {"location": signed, "x-dfo-commit": "a" * 40, "server": "x"}, b""
        ),
        capture.Response(
            200, {"content-type": "application/vnd.apache.parquet"}, b"PAR1"
        ),
        capture.Response(
            200, {"content-type": "application/vnd.apache.parquet"}, b"PAR2"
        ),
    ]
    sent: list[Mapping[str, str]] = []

    def transport(method: str, url: str, **kwargs: Any) -> Any:
        sent.append(kwargs["headers"])
        return replies.pop(0)

    recorder = capture.Recorder("some-name", "http://127.0.0.1:8000/api", transport)
    location = recorder.origin("GET", "/api/datasets/x/y/files/z").headers["location"]
    recorder.external(location)
    recorder.external(location)
    redacted = "https://storage.example/bucket/sha256%3Aab?X-Goog-Signature=redacted"
    assert recorder.exchanges[0]["response"] == {
        "status": 302,
        "headers": {"location": redacted},
    }
    assert recorder.exchanges[1]["request"] == {"method": "GET", "url": redacted}
    assert [e["response"]["body_file"] for e in recorder.exchanges[1:]] == [
        "some-name.parquet",
        "some-name-2.parquet",
    ]
    assert recorder.bodies == {
        "some-name.parquet": b"PAR1",
        "some-name-2.parquet": b"PAR2",
    }
    assert recorder.commits == {"a" * 40}
    assert sent[1:] == [{}, {}]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://storage.googleapis.com/b/o?X-Goog-Algorithm=A&X-Goog-Signature=s",
            "https://storage.googleapis.com/b/o?X-Goog-Signature=redacted",
        ),
        (
            "https://storage.googleapis.com/b/o?GoogleAccessId=sa%40p.iam.gserviceaccount.com&Signature=s",
            "https://storage.googleapis.com/b/o?X-Goog-Signature=redacted",
        ),
        (
            "https://storage.googleapis.com/b/o",
            "https://storage.googleapis.com/b/o?X-Goog-Signature=redacted",
        ),
        (
            "/api/datasets/operations/op_1?page=2",
            "/api/datasets/operations/op_1?page=2",
        ),
    ],
    ids=["v4-signed", "v2-signed", "no-query", "root-relative"],
)
def test_every_absolute_url_loses_its_query(url: str, expected: str) -> None:
    assert capture.redact_external_url(url) == expected


@pytest.mark.parametrize("name", [n for n in _records("synthetic") if n not in _LIVE])
def test_each_derived_record_follows_from_its_parent(name: str) -> None:
    record = _load(name)
    definition = next(d for d in capture.SYNTHETIC if d.name == name)
    parent = _load(record["origin"]["derived_from"])
    ctx = capture.Context(capture.DEV_BASE_URL, _Served(parent), ACTIONS)
    derived = definition.derive(copy.deepcopy(parent), ctx)
    assert record["origin"]["edit"] == definition.edit
    assert record["call"] == derived.call
    assert record["exchanges"] == derived.exchanges
    assert record["expect"] == derived.expect


def test_a_renamed_strict_field_ends_the_call_at_the_build() -> None:
    record = _load("fetch-renamed-strict-field")
    manifest = record["exchanges"][-1]["response"]["json"]
    assert "content_version" not in manifest
    assert "contentVersion" in manifest
    assert record["expect"][-1] == {"error": {"class": "ContractError", "code": None}}


def test_place_options_pin_each_candidate_on_the_ambiguous_wording() -> None:
    body = _load("search-place-ambiguous")["exchanges"][0]["response"]["json"]
    no_data = capture.with_place_options(body)["parts"][0]["no_data"]
    assert "data_query" not in no_data
    assert [o["label"] for o in no_data["options"]] == [
        "Congo [DRC]",
        "Congo [Republic]",
    ]
    assert [o["mentions"] for o in no_data["options"]] == [["Congo"], ["Congo"]]
    pins = [
        {e["text"]: e.get("dcid") for e in o["data_query"]["parts"][0]["entities"]}
        for o in no_data["options"]
    ]
    assert pins == [
        {"Germany": "country/DEU", "Congo": "country/COD"},
        {"Germany": "country/DEU", "Congo": "country/COG"},
    ]
    assert "options" not in body["parts"][0]["no_data"], "the input is left unchanged"


@pytest.mark.parametrize(
    ("timed_out", "status", "expected"),
    [
        (False, "no_data", "not_understood"),
        (True, "no_data", "timed_out"),
        (False, "unsupported", "unsupported"),
        (False, "deferred", "unknown"),
    ],
)
def test_a_part_without_a_reading_is_a_gap_of_its_kind(
    timed_out: bool, status: str, expected: str
) -> None:
    body = copy.deepcopy(
        _load("answer-mixed-fetch-raises")["exchanges"][0]["response"]["json"]
    )
    body["diagnostics"]["timed_out"] = timed_out
    body["parts"][1]["status"] = status
    gaps = capture.answer_gaps(
        capture.answer_from_body(body), capture.gap_kinds(ACTIONS)
    )
    assert gaps == [{"part": 1, "kind": expected, "options": 0}]


def test_several_members_stack_with_a_part_column_first() -> None:
    member = capture.Member(
        [], {"spec_id": "spec_a"}, "cv_a", "A.", "sha256:a", 2, ["x", "y"]
    )
    other = capture.Member(
        [], {"spec_id": "spec_b"}, "cv_b", "B.", "sha256:b", 3, ["y", "z"]
    )
    unnamed = capture.Member(
        ["p", "q"], {"spec_id": "spec_b"}, "cv_b", "B.", "sha256:b", 3, ["y", "z"]
    )
    dataset = capture.dataset_outcome([member, unnamed])["dataset"]
    assert dataset["columns"] == ["part", "x", "y", "z"]
    assert dataset["rows"] == 5
    assert dataset["part_rows"] == [
        {"part": "A.", "rows": 2},
        {"part": "p; q", "rows": 3},
    ]
    assert "part_rows" not in capture.dataset_outcome([other])["dataset"]
    assert capture.dataset_outcome([member])["dataset"]["columns"] == ["x", "y"]


@pytest.mark.parametrize(
    "path",
    [
        capture.PACKAGE_DIR / "served" / "openapi.json",
        capture.PACKAGE_DIR / "served" / "SOURCE.json",
        SCENARIOS / "answer-definite-fetch.json",
    ],
)
def test_the_writer_keeps_the_kit_json_formatting(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert capture.dump_json(json.loads(text)) == text


def test_a_rewritten_record_drops_the_body_files_it_no_longer_names(
    tmp_path: Path,
) -> None:
    def record(*body_files: str) -> dict[str, Any]:
        exchanges = [{"response": {"status": 200, "body_file": f}} for f in body_files]
        return {"name": "s", "exchanges": exchanges}

    capture.write_record(
        tmp_path,
        record("s.parquet", "s-2.parquet"),
        {"s.parquet": b"1", "s-2.parquet": b"2"},
    )
    (tmp_path / "s-other.parquet").write_bytes(b"kept")
    (tmp_path / "parent.parquet").write_bytes(b"kept")
    capture.write_record(
        tmp_path, record("s.parquet", "parent.parquet"), {"s.parquet": b"3"}
    )
    capture.write_record(tmp_path, record("s.parquet"), {"s.parquet": b"3"})
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "parent.parquet",
        "s-other.parquet",
        "s.json",
        "s.parquet",
    ]
    assert (tmp_path / "s.parquet").read_bytes() == b"3"


def test_the_capture_environment_takes_nothing_else_from_the_caller(
    tmp_path: Path,
) -> None:
    caller = {
        "PATH": "/bin",
        "HOME": "/home/x",
        "DFO_DATASETS_SURFACE": "0",
        "GEMINI_API_KEY": "k",
    }
    env = capture.capture_env(tmp_path, caller)
    assert env["DFO_DATASETS_SURFACE"] == "1"
    assert "GEMINI_API_KEY" not in env
    assert env["PATH"] == "/bin"
    assert (
        env["COVERAGE_ARTIFACT_URI"]
        == (tmp_path / "tests" / "fixtures" / "coverage_slice").as_uri()
    )


def _checkout(path: Path) -> Path:
    """A git checkout with one committed file."""

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    git("init", "-q")
    (path / "f").write_text("a", encoding="utf-8")
    git("add", "f")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "f")
    return path


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (
            lambda p: (p / "f").write_text("b", encoding="utf-8"),
            "uncommitted changes or untracked files",
        ),
        (
            lambda p: (p / "stray.py").write_text("", encoding="utf-8"),
            "uncommitted changes or untracked files",
        ),
    ],
    ids=["tracked-change", "untracked-file"],
)
def test_a_checkout_that_is_not_its_commit_is_refused(
    tmp_path: Path, change: Callable[[Path], None], refusal: str
) -> None:
    checkout = _checkout(tmp_path)
    change(checkout)
    with pytest.raises(SystemExit, match=refusal):
        capture.capture_openapi(checkout)


def test_an_environment_out_of_step_with_the_lock_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv reports the environment differs, so nothing runs in it."""
    checkout = _checkout(tmp_path)
    run = subprocess.run
    commands: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        if args[0] != "uv":
            return run(args, **kwargs)
        commands.append(args)
        return subprocess.CompletedProcess(args, 1, "", "Would install 3 packages")

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    with pytest.raises(SystemExit, match=r"differs from its uv\.lock"):
        capture.capture_openapi(checkout)
    (command,) = commands
    assert command[:2] == ["uv", "sync"]
    assert {"--frozen", "--check"} <= set(command)
    assert {a for a in command if a.startswith("--extra=")} == {
        f"--extra={e}" for e in ("eval", "api", "engine", "datasets", "graph")
    }
