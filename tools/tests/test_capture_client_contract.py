"""Tests for tools/capture_client_contract.py.

The recorder's client reproduces each packaged record from the record's own
steps and responses, every derived record follows from its parent, and the
writers keep the kit's JSON formatting. No test reaches the network: a fake
transport serves the recorded responses, and the Parquet shape reader is
replaced, so rows and columns are the one part of a dataset outcome these
tests leave to the recording.

Run with: uv run --with jsonschema --with pyarrow --with pytest pytest tools/tests -q
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
CLIENT_CODES = capture.load_client_codes(capture.PACKAGE_DIR)


def _load(name: str) -> dict[str, Any]:
    return json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))


def _records(kind: str) -> list[str]:
    return sorted(
        p.stem
        for p in SCENARIOS.glob("*.json")
        if json.loads(p.read_text(encoding="utf-8"))["origin"]["kind"] == kind
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


def test_every_record_has_a_definition_and_every_definition_a_record() -> None:
    for kind, definitions in (
        ("recorded", capture.RECORDED),
        ("synthetic", capture.SYNTHETIC),
    ):
        assert sorted(d.name for d in definitions) == _records(kind)


@pytest.mark.parametrize("name", _records("recorded"))
def test_the_recorder_reproduces_each_record_from_its_steps(name: str) -> None:
    record = _load(name)
    served = capture.Served(record, SCENARIOS)
    recorder = capture.Recorder(name, served.base_url, served)
    session = capture.Session(
        recorder, ACTIONS, CLIENT_CODES, read_shape=_shape, sleep=lambda _: None
    )
    capture.drive(session, record["call"])
    assert served.pending == [], "the client skipped a recorded exchange"
    assert session.call == record["call"]
    assert recorder.exchanges == record["exchanges"]
    assert _without_shapes(session.expect) == _without_shapes(record["expect"])


def test_a_recorder_keeps_bodies_redacts_signed_urls_and_names_the_commit() -> None:
    signed = "https://storage.example/bucket/sha256%3Aab?X-Goog-Algorithm=GOOG4&X-Goog-Signature=f00"
    replies = [
        capture.Response(
            302,
            {
                "location": signed,
                "x-dfo-commit": "a" * 40,
                "x-dfo-trace-id": "t" * 32,
                "server": "x",
            },
            b"",
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
        "headers": {"location": redacted, "x-dfo-trace-id": "t" * 32},
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
    ("headers", "refused"),
    [
        ({}, True),
        ({"cache-control": "no-store"}, True),
        ({"cache-control": "public, max-age=60"}, False),
        ({"x-dfo-trace-id": "t" * 32}, False),
    ],
    ids=["untraced", "untraced-private", "shared-cacheable", "traced"],
)
def test_a_recorder_refuses_an_api_response_without_a_trace_id(
    headers: dict[str, str], refused: bool
) -> None:
    """The server sends x-dfo-trace-id on every API response but a
    shared-cacheable one, so a recording that lacks it would hold an
    exchange the server did not make."""
    reply = capture.Response(
        200, {"content-type": "application/json", **headers}, b"{}"
    )
    recorder = capture.Recorder(
        "some-name", "http://127.0.0.1:8000/api", lambda *_, **__: reply
    )
    if refused:
        with pytest.raises(SystemExit, match="x-dfo-trace-id"):
            recorder.api("GET", "/search/healthz")
    else:
        assert recorder.api("GET", "/search/healthz").status == 200


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
        (
            "http://127.0.0.1:19493/__e2e__/blob/e2e-working/sha256:ab?response_type=x",
            "https://storage.googleapis.com/e2e-working/sha256%3Aab?X-Goog-Signature=redacted",
        ),
    ],
    ids=["v4-signed", "v2-signed", "no-query", "root-relative", "local-blob"],
)
def test_every_absolute_url_loses_its_query(url: str, expected: str) -> None:
    assert capture.redact_external_url(url) == expected


@pytest.mark.parametrize(
    ("location", "refused"),
    [
        ("http://127.0.0.1:19493/__e2e__/blob/b/o?X-Goog-Signature=redacted", True),
        ("http://localhost:19493/b/o?X-Goog-Signature=redacted", True),
        ("http://127.0.0.1:4443/b/o?X-Goog-Signature=redacted", True),
        ("http://127.0.0.1/b/o?X-Goog-Signature=redacted", True),
        ("http://storage.googleapis.com/b/o?X-Goog-Signature=redacted", True),
        ("https://storage.googleapis.com/b/o?X-Goog-Signature=redacted", False),
        ("http://127.0.0.1:19493/api/datasets/operations/op_1", False),
        ("/api/datasets/operations/op_1", False),
    ],
    ids=[
        "local-signed-url",
        "localhost-alias",
        "other-port",
        "no-port",
        "storage-over-http",
        "storage-url",
        "api-route-under-the-base-url",
        "root-relative",
    ],
)
def test_a_record_whose_signed_url_names_the_local_host_is_refused(
    location: str, refused: bool
) -> None:
    exchange = {
        "request": {"method": "GET", "path": "/datasets/s/v/file"},
        "response": {"status": 302, "headers": {"location": location}},
    }
    record = {"name": "s", "exchanges": [exchange]}
    base_url = "http://127.0.0.1:19493/api"
    if refused:
        with pytest.raises(SystemExit, match="where a record's absolute URL names"):
            capture._refuse_local_host(record, base_url)
    else:
        capture._refuse_local_host(record, base_url)


@pytest.mark.parametrize("name", _records("synthetic"))
def test_each_derived_record_follows_from_its_parent(name: str) -> None:
    record = _load(name)
    definition = next(d for d in capture.SYNTHETIC if d.name == name)
    parent = _load(record["origin"]["derived_from"])
    ctx = capture.Context(
        capture.DEV_BASE_URL, capture.Served(parent, SCENARIOS), ACTIONS, CLIENT_CODES
    )
    derived = definition.derive(copy.deepcopy(parent), ctx)
    assert record["origin"]["edit"] == definition.edit
    assert record["call"] == derived.call
    assert record["exchanges"] == derived.exchanges
    assert record["expect"] == derived.expect


def test_an_offline_rederivation_rewrites_only_the_expect(tmp_path: Path) -> None:
    name = "answer-declined"
    for path in SCENARIOS.glob(f"{name}*"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    stale = _load(name)
    stale["expect"][1]["error"]["code"] = None
    capture.write_record(tmp_path, stale, {})
    ctx = capture.Context(
        capture.DEV_BASE_URL, capture._offline, ACTIONS, CLIENT_CODES, read_shape=_shape
    )
    capture.rederive_recorded(name, ctx, tmp_path)
    fresh = (tmp_path / f"{name}.json").read_text(encoding="utf-8")
    assert fresh == (SCENARIOS / f"{name}.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("named", ["symlink", "parent", "absolute"])
def test_an_offline_rederivation_refuses_a_body_file_outside_its_directory(
    tmp_path: Path, named: str
) -> None:
    """A body file that is a symlink, or a name that leaves the scenarios
    directory, is refused before its bytes are read into a record."""
    name = "answer-definite-fetch"
    scenarios, outside = tmp_path / "scenarios", tmp_path / "outside.parquet"
    scenarios.mkdir()
    for path in SCENARIOS.glob(f"{name}.*"):
        (scenarios / path.name).write_bytes(path.read_bytes())
    body = scenarios / f"{name}.parquet"
    body.rename(outside)
    body_file = {
        "symlink": body.name,
        "parent": f"../{outside.name}",
        "absolute": str(outside),
    }[named]
    if named == "symlink":
        body.symlink_to(outside)
    record = scenarios / f"{name}.json"
    stale = json.loads(record.read_text(encoding="utf-8"))
    for exchange in stale["exchanges"]:
        if "body_file" in exchange["response"]:
            exchange["response"]["body_file"] = body_file
    record.write_text(json.dumps(stale), encoding="utf-8")
    ctx = capture.Context(
        capture.DEV_BASE_URL, capture._offline, ACTIONS, CLIENT_CODES, read_shape=_shape
    )
    with pytest.raises(SystemExit) as refused:
        capture.rederive_recorded(name, ctx, scenarios)
    message = str(refused.value)
    assert f"body file {body_file!r}" in message
    assert "record it live" not in message
    assert json.loads(record.read_text(encoding="utf-8")) == stale
    assert body.is_symlink() == (named == "symlink")


def test_a_writer_refuses_a_symlinked_body_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"outside")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "s.parquet").symlink_to(outside)
    record = {"name": "s", "exchanges": [{"response": {"body_file": "s.parquet"}}]}
    with pytest.raises(ValueError, match="symlink"):
        capture.write_record(scenarios, record, {"s.parquet": b"new"})
    assert outside.read_bytes() == b"outside"
    assert sorted(p.name for p in scenarios.iterdir()) == ["s.parquet"]


def test_a_recorder_refuses_a_request_that_is_not_the_next_canned_one() -> None:
    def transport(method: str, url: str, **kwargs: Any) -> Any:
        raise AssertionError(f"{method} {url} reached the network")

    canned = [
        {
            "request": {"method": "GET", "path": "/datasets/a"},
            "response": {"status": 200},
        }
    ]
    recorder = capture.Recorder("s", capture.DEV_BASE_URL, transport, canned=canned)
    with pytest.raises(SystemExit, match="is not the canned request"):
        recorder.api("GET", "/datasets/b")


def test_a_recorded_scenario_answered_by_a_canned_exchange_is_refused(
    tmp_path: Path,
) -> None:
    canned = {
        "request": {"method": "GET", "path": "/datasets/a"},
        "response": {"status": 200, "json": {}},
    }

    def run(s: Any, ctx: Any) -> None:
        s.recorder.canned = [canned]
        s.recorder.api("GET", "/datasets/a")

    def transport(method: str, url: str, **kwargs: Any) -> Any:
        raise AssertionError(f"{method} {url} reached the network")

    definition = capture.Recorded("s", "None.", run)
    ctx = capture.Context(capture.DEV_BASE_URL, transport, ACTIONS, CLIENT_CODES)
    with pytest.raises(SystemExit, match="1 were canned"):
        capture.record_recorded(definition, ctx, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_synthetic_derivation_makes_no_request(tmp_path: Path) -> None:
    (tmp_path / "parent.json").write_text(json.dumps({"exchanges": []}))

    def derive(parent: dict[str, Any], ctx: Any) -> Any:
        ctx.session("synthetic").search("anything")

    def transport(method: str, url: str, **kwargs: Any) -> Any:
        raise AssertionError(f"{method} {url} reached the network")

    definition = capture.Synthetic("s", "parent", "None.", "None.", derive)
    ctx = capture.Context(capture.DEV_BASE_URL, transport, ACTIONS, CLIENT_CODES)
    with pytest.raises(SystemExit, match="makes no request"):
        capture.record_synthetic(definition, ctx, tmp_path)


def test_a_renamed_strict_field_ends_the_call_at_the_build() -> None:
    record = _load("fetch-renamed-strict-field")
    response = record["exchanges"][-1]["response"]
    manifest = response["json"]
    assert "content_version" not in manifest
    assert "contentVersion" in manifest
    assert record["expect"][-1] == {
        "error": {
            "class": "ContractError",
            "code": "contract_violation",
            "trace_id": response["headers"]["x-dfo-trace-id"],
        }
    }


@pytest.mark.parametrize(
    ("timed_out", "status", "reason", "expected"),
    [
        (False, "no_data", "variable_not_resolved", "not_understood"),
        (True, "no_data", "variable_not_resolved", "not_understood"),
        (False, "no_data", "timed_out", "timed_out"),
        (False, "unsupported", "variable_not_resolved", "unsupported"),
        (False, "deferred", "variable_not_resolved", "unknown"),
    ],
)
def test_a_part_without_a_reading_is_a_gap_of_its_kind(
    timed_out: bool, status: str, reason: str, expected: str
) -> None:
    body = copy.deepcopy(
        _load("answer-mixed-fetch-raises")["exchanges"][0]["response"]["json"]
    )
    body["diagnostics"]["timed_out"] = timed_out
    body["parts"][1]["status"] = status
    body["parts"][1]["no_data"]["reason"] = reason
    gaps = capture.answer_gaps(
        capture.answer_from_body(body), capture.gap_kinds(ACTIONS)
    )
    assert gaps == [{"part": 1, "kind": expected, "options": 0}]


def test_several_members_stack_with_a_part_column_first() -> None:
    """The stacked columns follow `part` in the column-order rule's order,
    the reading columns first and `stat_var` last."""
    member = capture.Member(
        [],
        {"spec_id": "spec_a"},
        "cv_a",
        "A.",
        "sha256:a",
        2,
        ["stat_var", "x", "value"],
    )
    other = capture.Member(
        [], {"spec_id": "spec_b"}, "cv_b", "B.", "sha256:b", 3, ["y", "z"]
    )
    unnamed = capture.Member(
        ["p", "q"],
        {"spec_id": "spec_b"},
        "cv_b",
        "B.",
        "sha256:b",
        3,
        ["value", "time_period"],
    )
    dataset = capture.dataset_outcome([member, unnamed])["dataset"]
    assert dataset["columns"] == ["part", "time_period", "value", "x", "stat_var"]
    assert dataset["rows"] == 5
    assert dataset["part_rows"] == [
        {"part": "A.", "rows": 2},
        {"part": "p; q", "rows": 3},
    ]
    assert "part_rows" not in capture.dataset_outcome([other])["dataset"]
    columns = capture.dataset_outcome([member])["dataset"]["columns"]
    assert columns == ["value", "x", "stat_var"]


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


def test_the_capture_environment_takes_nothing_else_from_the_caller() -> None:
    caller = {
        "PATH": "/bin",
        "HOME": "/home/x",
        "DFO_DATASETS_SURFACE": "0",
        "GEMINI_API_KEY": "k",
    }
    env = capture.capture_env(caller)
    assert env["DFO_DATASETS_SURFACE"] == "1"
    assert "GEMINI_API_KEY" not in env
    assert env["PATH"] == "/bin"


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
