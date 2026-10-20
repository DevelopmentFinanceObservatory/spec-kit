# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
#     "pyarrow>=15",
# ]
# ///
"""Capture the client contract's served document and record its scenarios.

Usage:
    uv run tools/capture_client_contract.py openapi --dfo-apis PATH
    uv run tools/capture_client_contract.py record [--base-url URL]
        [--only NAME ...] [--offline] [--scenarios-dir DIR]

`openapi` builds the composed dfo-apis app from the checkout at PATH, every
surface flag on, by calling `dfo_apis.app.create_app()` in that checkout's
environment, and writes `app.openapi()` to `served/openapi.json` and the
checkout's commit to `served/SOURCE.json`. No server runs. The surfaces load
an origin catalogue and a coverage index when they are built, so the capture
gives them the checkout's own test fixtures; the document does not depend on
the data they hold. A checkout with uncommitted changes to tracked files is
refused, because the commit would not describe the document.

`record` runs every scenario this file defines against the API at the base
URL (dev by default; a local app at `http://127.0.0.1:8083/api`), the way a
client would, and writes each record and its binary bodies to `scenarios/`.
Each recorded scenario checks that its live answer still has the shape the
scenario is named for and refuses to write one that does not. With
`--offline` no request is made: each recorded scenario's steps run again
over its own exchanges and rewrite its `expect`, and each synthetic scenario
that records nothing live is derived again from its parent. Synthetic
scenarios are derived from the recorded records after them, so a recording
refreshes both, and a synthetic record names its parent's body files rather
than copies. Every absolute URL a record keeps, a signed download or a
`location` header, keeps its path, and its query becomes
`X-Goog-Signature=redacted`. The run ends by validating the whole contract
with `tools/validate_client_contract.py`, and fails when it reports a
problem or the binary bodies pass 5 MB.

The outcome rules, error classes and gap kinds come from
`tools/validate_client_contract.py`, so a record's `expect` is written by
the code that checks it. pyarrow loads only when a Parquet body is read, so
the tests run without it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from validate_client_contract import (
    UNNAMED_VARIABLE_TEXT,
    Answer,
    ClientError,
    answer_from_body,
    answer_gaps,
    answer_members,
    answer_outcome,
    candidate_answer,
    choice_for,
    choice_input,
    dataset_summary,
    gap_kinds,
    owns_body_file,
    part_label,
    refusal_error,
    search_warning_code,
    search_warns,
)
from validate_client_contract import main as validate_main

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "python" / "client-contract" / "src" / "dfo_client_contract"
DEV_BASE_URL = "https://dfo-dev-apis-zms5lj4zhq-nn.a.run.app/api"
REPOSITORY = "DevelopmentFinanceObservatory/apis"
BINARY_BUDGET = 5_000_000

# The rendering both clients ask for: bare long Parquet.
RENDERING = {"format": "parquet", "bundle": False}
# The response headers a client reads, and so the ones a record keeps.
KEPT_HEADERS = ("content-type", "location", "retry-after")
POLL_PATH = "/datasets/operations/{operation_id}"


def dump_json(document: Any) -> str:
    """`document` in the kit's JSON formatting: two-space indent, non-ASCII
    kept, one trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _write_json(path: Path, document: Any) -> None:
    path.write_text(dump_json(document), encoding="utf-8")


# --- the served document ----------------------------------------------------

# Runs in the dfo-apis checkout's environment, with the checkout as cwd.
_CAPTURE_SCRIPT = """
import json, os, sys, tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    from tests.origin_catalogue.support import publish_sample

    os.environ["ORIGIN_CATALOGUE_URI"] = f"file://{tmp}/origin-catalogue"
    publish_sample(os.environ["ORIGIN_CATALOGUE_URI"])
    from dfo_apis.app import create_app

    document = create_app().openapi()
Path(sys.argv[1]).write_text(json.dumps(document), encoding="utf-8")
"""

# Placeholders the surfaces need to be built. None of them reaches the document.
_CAPTURE_ENV = {
    "SEARCH_GRAPH_BASE": "https://graph.offline.invalid",
    "DFO_PUBLIC_SITE_URL": "https://site.example",
    "COVERAGE_RECHECK_SECONDS": "0",
    "COVERAGE_INDEX_CHECK": "off",
    "DFO_DATASETS_SURFACE": "1",
    "DFO_GRAPH_SURFACE": "1",
    "DFO_PRESENTATION_SURFACE": "1",
    "DFO_SKILLS_SURFACE": "1",
}
_PASSED_ENV = ("HOME", "PATH", "TMPDIR", "UV_CACHE_DIR", "UV_PYTHON")
# The dfo-apis extras the composed app imports.
_EXTRAS = tuple(
    f"--extra={extra}" for extra in ("eval", "api", "engine", "datasets", "graph")
)


def capture_env(checkout: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """The capture's environment: a few of the caller's variables and the
    placeholders, so a variable in the caller's shell cannot reach the app."""
    env = {name: environ[name] for name in _PASSED_ENV if name in environ}
    env.update(_CAPTURE_ENV)
    coverage = checkout / "tests" / "fixtures" / "coverage_slice"
    env["COVERAGE_ARTIFACT_URI"] = coverage.as_uri()
    return env


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def capture_openapi(checkout: Path) -> tuple[dict[str, Any], str]:
    """`app.openapi()` of the composed app in `checkout`, and its commit.

    Refuses a checkout with uncommitted changes or untracked files, and an
    environment that differs from the checkout's `uv.lock` with every extra
    the composed app needs, since either would make the commit misdescribe
    the document."""
    if _git(checkout, "status", "--porcelain"):
        raise SystemExit(f"{checkout} has uncommitted changes or untracked files")
    commit = _git(checkout, "rev-parse", "HEAD")
    check = subprocess.run(
        ["uv", "sync", "--project", str(checkout), "--frozen", "--check", *_EXTRAS],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode != 0:
        raise SystemExit(
            f"the environment in {checkout} differs from its uv.lock; run "
            f"`uv sync --frozen {' '.join(_EXTRAS)}` there:\n{check.stderr}"
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "openapi.json"
        result = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(checkout),
                "--frozen",
                "--no-sync",
                "python",
                "-c",
                _CAPTURE_SCRIPT,
                str(out),
            ],
            cwd=checkout,
            env=capture_env(checkout, os.environ),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(f"the capture failed in {checkout}:\n{result.stderr}")
        return json.loads(out.read_text(encoding="utf-8")), commit


def write_served(
    package_dir: Path, document: dict[str, Any], commit: str, captured_from: str
) -> None:
    served = package_dir / "served"
    _write_json(served / "openapi.json", document)
    _write_json(
        served / "SOURCE.json",
        {
            "format_version": 1,
            "repository": REPOSITORY,
            "commit": commit,
            "captured_from": captured_from,
        },
    )


# --- transport and recorder -------------------------------------------------


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


class Transport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Leaves a redirect to the caller: the files route's 302 is read by hand."""

    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


def urllib_transport(
    method: str,
    url: str,
    *,
    body: bytes | None,
    headers: Mapping[str, str],
    timeout: float,
) -> Response:
    request = urllib.request.Request(
        url, data=body, method=method, headers=dict(headers)
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as reply:
            return Response(reply.status, _lower(reply.headers.items()), reply.read())
    except urllib.error.HTTPError as error:
        return Response(error.code, _lower(error.headers.items()), error.read())


def _lower(items: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in items}


def redact_external_url(url: str) -> str:
    """An absolute `url` with its query, whatever signs it, replaced by
    `X-Goog-Signature=redacted`; a root-relative URL as it stands."""
    parts = urlsplit(url)
    if not parts.netloc:
        return url
    return urlunsplit(parts._replace(query="X-Goog-Signature=redacted"))


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _is_json(content_type: str) -> bool:
    media = content_type.split(";", maxsplit=1)[0].strip()
    return media == "application/json" or media.endswith("+json")


_EXTENSIONS = {"application/vnd.apache.parquet": "parquet"}


@dataclass
class Recorder:
    """Sends requests the way a client does and keeps each exchange.

    `canned` exchanges are served in order, before the network, to the
    requests that match them; `edit` rewrites each live JSON response body."""

    name: str
    base_url: str
    transport: Transport
    canned: list[dict[str, Any]] = field(default_factory=list)
    edit: Callable[[Any], Any] | None = None
    exchanges: list[dict[str, Any]] = field(default_factory=list)
    bodies: dict[str, bytes] = field(default_factory=dict)
    commits: set[str] = field(default_factory=set)

    def api(self, method: str, path: str, json_body: Any = None) -> Response:
        """A request to `path` under the base URL."""
        return self._send(
            method, {"path": path}, self.base_url.rstrip("/") + path, json_body
        )

    def origin(self, method: str, origin_path: str) -> Response:
        """A request to a root-relative URL the server returned."""
        url = _origin(self.base_url) + origin_path
        return self._send(method, {"origin_path": origin_path}, url, None)

    def external(self, url: str) -> Response:
        """A bare GET outside the API, with no client header and no key."""
        return self._send(
            "GET", {"url": redact_external_url(url)}, url, None, bare=True
        )

    def _send(
        self,
        method: str,
        target: dict[str, str],
        url: str,
        json_body: Any,
        *,
        bare: bool = False,
    ) -> Response:
        request: dict[str, Any] = {"method": method, **target}
        if json_body is not None:
            request["json"] = json_body
        if self.canned and self.canned[0]["request"] == request:
            exchange = self.canned.pop(0)
            self.exchanges.append(exchange)
            return _canned_response(exchange["response"])

        headers = {} if bare else {"user-agent": "dfo-client-contract-recorder/1"}
        body = None
        if json_body is not None:
            headers["content-type"] = "application/json"
            body = json.dumps(json_body).encode()
        response = self.transport(
            method, url, body=body, headers=headers, timeout=300.0
        )
        if not bare and "x-dfo-commit" in response.headers:
            self.commits.add(response.headers["x-dfo-commit"])
        if self.edit is not None and _is_json(response.headers.get("content-type", "")):
            edited = json.dumps(self.edit(response.json())).encode()
            response = Response(response.status, response.headers, edited)
        self.exchanges.append({"request": request, "response": self._keep(response)})
        return response

    def _keep(self, response: Response) -> dict[str, Any]:
        kept: dict[str, Any] = {"status": response.status}
        headers = {
            name: redact_external_url(response.headers[name])
            if name == "location"
            else response.headers[name]
            for name in KEPT_HEADERS
            if name in response.headers
        }
        if headers:
            kept["headers"] = headers
        content_type = response.headers.get("content-type", "")
        if response.body and _is_json(content_type):
            kept["json"] = response.json()
        elif response.body and 200 <= response.status < 300:
            extension = _EXTENSIONS.get(
                content_type.split(";", maxsplit=1)[0].strip(), "bin"
            )
            stem = (
                self.name if not self.bodies else f"{self.name}-{len(self.bodies) + 1}"
            )
            body_file = f"{stem}.{extension}"
            self.bodies[body_file] = response.body
            kept["body_file"] = body_file
        return kept


def _canned_response(response: dict[str, Any]) -> Response:
    headers = dict(response.get("headers", {}))
    if "json" in response:
        return Response(
            response["status"], headers, json.dumps(response["json"]).encode()
        )
    return Response(response["status"], headers, b"")


# --- the answer and dataset rules -------------------------------------------


@dataclass(frozen=True, slots=True)
class Spec:
    spec_id: str
    definition: dict[str, Any]
    sentence: str | None = None

    def step(self) -> dict[str, Any]:
        step: dict[str, Any] = {"spec_id": self.spec_id, "definition": self.definition}
        if self.sentence is not None:
            step["sentence"] = self.sentence
        return step


def _part_spec(spec: dict[str, Any]) -> Spec:
    return Spec(
        spec["spec_id"], spec["definition"], spec["resolved"]["rendered_sentence"]
    )


def _members(answer: Answer) -> list[tuple[list[str], Spec]]:
    return [(parts, _part_spec(spec)) for parts, spec in answer_members(answer)]


@dataclass(frozen=True, slots=True)
class Member:
    parts: list[str]
    definition: dict[str, Any]
    content_version: str
    sentence: str
    digest: str
    rows: int
    columns: list[str]

    @property
    def spec_id(self) -> str:
        return self.definition["spec_id"]

    def ref(self) -> dict[str, Any]:
        return {
            "parts": self.parts,
            "sentence": self.sentence,
            "spec": {"spec_id": self.spec_id, "definition": self.definition},
            "content_version": self.content_version,
            "artifact": {"digest": self.digest, "rendering": dict(RENDERING)},
        }


def dataset_outcome(
    members: Sequence[Member],
    *,
    missing: Sequence[Mapping[str, Any]] = (),
    search_warning: bool | None = None,
) -> dict[str, Any]:
    """A dataset outcome. Several members stack into one long table with a
    `part` column first, holding each member's `part_label`, then every
    member's columns in member order."""
    summaries = [
        {
            "parts": member.parts,
            "spec_id": member.spec_id,
            "content_version": member.content_version,
            "sentence": member.sentence,
        }
        for member in members
    ]
    dataset: dict[str, Any] = {"rows": sum(member.rows for member in members)}
    if len(members) == 1:
        dataset["columns"] = list(members[0].columns)
    else:
        columns = dict.fromkeys(c for m in members for c in m.columns)
        dataset["columns"] = ["part", *columns]
        dataset["part_rows"] = [
            {"part": part_label(summary), "rows": member.rows}
            for summary, member in zip(summaries, members, strict=True)
        ]
    summary = dataset_summary(summaries, missing, search_warning=search_warning)
    return {"dataset": {**dataset, **summary}}


def parquet_shape(data: bytes) -> tuple[int, list[str]]:
    import pyarrow.parquet as pq  # noqa: PLC0415 - optional, so the tests run without it

    parquet = pq.ParquetFile(io.BytesIO(data))
    return parquet.metadata.num_rows, list(parquet.schema_arrow.names)


def _reached[T](results: list[T], step: str) -> T:
    """The result a step produced; a step that refused cannot be acted on."""
    if not results:
        raise ShapeError(f"{step} refused, so the steps after it cannot run")
    return results[0]


class ShapeError(Exception):
    """The live answer no longer has the shape the scenario is named for."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ShapeError(message)


@dataclass
class Session:
    """A client over a recorder: each step appends to `call` and `expect`."""

    recorder: Recorder
    actions: Mapping[str, Mapping[str, Any]]
    client_codes: Mapping[str, Mapping[str, Any]]
    read_shape: Callable[[bytes], tuple[int, list[str]]] = parquet_shape
    sleep: Callable[[float], None] = time.sleep
    call: list[dict[str, Any]] = field(default_factory=list)
    expect: list[dict[str, Any]] = field(default_factory=list)
    minted: tuple[Spec, dict[str, Any]] | None = None

    @property
    def gap_kinds(self) -> dict[str, str]:
        return gap_kinds(self.actions)

    def _step(self, step: dict[str, Any], run: Callable[[], dict[str, Any]]) -> None:
        self.call.append(step)
        try:
            outcome = run()
        except ClientError as error:
            outcome = error.outcome(self.client_codes)
        self.expect.append(outcome)

    def _answered(self, answer: Answer) -> dict[str, Any]:
        return answer_outcome(answer, self.gap_kinds)

    def _resolve(self, input_: dict[str, Any]) -> Answer:
        response = self.recorder.api(
            "POST", "/search/resolve", {"schema_version": 1, "input": input_}
        )
        self._raise_problem(response)
        return answer_from_body(response.json())

    def search(self, question: str) -> Answer:
        answers: list[Answer] = []

        def run() -> dict[str, Any]:
            answers.append(self._resolve({"kind": "raw_text", "query": question}))
            return self._answered(answers[0])

        self._step({"op": "search", "question": question}, run)
        return _reached(answers, "the search")

    def choose(
        self, answer: Answer, option: int | str, *, part: int | str | None = None
    ) -> Answer | None:
        """Settles a part by the rules `choice_for` and `choice_input` state,
        and returns the new answer, or None when the choice refused.

        A choice among one part's candidates is local; any other choice posts
        to Search."""
        step: dict[str, Any] = {"op": "choose"}
        if part is not None:
            step["part"] = part
        step["option"] = option
        answers: list[Answer] = []

        def run() -> dict[str, Any]:
            choice = choice_for(answer, self.gap_kinds, part, option)
            posts = choice_input(answer, choice)
            if posts is not None:
                answers.append(self._resolve(posts))
            elif choice.candidate is not None:
                answers.append(candidate_answer(answer, choice.part, choice.candidate))
            return self._answered(answers[0])

        self._step(step, run)
        return answers[0] if answers else None

    def fetch_answer(
        self, answer: Answer, *, partial: bool = False, strict: bool = False
    ) -> None:
        """Builds the answer's readings. A declined or catalogue answer, a gap
        outside a partial fetch, a partial fetch with no reading, and a strict
        fetch whose readings depart from the question each raise before any
        request."""
        step: dict[str, Any] = {"op": "fetch"}
        if partial:
            step["partial"] = True
        if strict:
            step["strict"] = True

        def run() -> dict[str, Any]:
            if answer.body["kind"] != "resolution":
                raise ClientError("answer_declined")
            gaps = answer_gaps(answer, self.gap_kinds)
            readings = _members(answer)
            if gaps and (not partial or not readings):
                raise ClientError("answer_incomplete")
            warns = search_warns(answer)
            if warns and strict:
                raise ClientError(search_warning_code(answer), error="SearchWarning")
            members = [self.build(parts, spec, None) for parts, spec in readings]
            return dataset_outcome(members, missing=gaps, search_warning=warns)

        self._step(step, run)

    def fetch_spec(
        self,
        spec: Spec,
        *,
        window: str | tuple[int, int] | None = None,
        facets: Mapping[str, str] | None = None,
    ) -> list[Member]:
        step: dict[str, Any] = {"op": "fetch", "spec": spec.step()}
        scope: dict[str, Any] = {}
        if window is not None:
            step["window"] = list(window) if isinstance(window, tuple) else window
            scope["window"] = _window(window)
        if facets is not None:
            step["facets"] = dict(facets)
            scope["facets"] = {name: {"ref": ref} for name, ref in facets.items()}
        built: list[Member] = []

        def run() -> dict[str, Any]:
            built.append(self.build([], spec, scope or None))
            return dataset_outcome(built)

        self._step(step, run)
        return built

    def fetch_ref(self, ref: dict[str, Any]) -> None:
        """The exact versions a ref names, through the snapshot and files routes."""

        def run() -> dict[str, Any]:
            members = []
            for member in ref["members"]:
                spec_id = member["spec"]["spec_id"]
                cv = member["content_version"]
                response = self.recorder.api("GET", f"/datasets/{spec_id}/{cv}")
                self._raise_problem(response)
                digest = member["artifact"]["digest"]
                artifact = next(
                    a
                    for a in response.json()["artifacts"]
                    if a["artifact_digest"] == digest
                )
                rows, columns = self.read_shape(self._download(artifact))
                members.append(
                    Member(
                        member["parts"],
                        member["spec"]["definition"],
                        cv,
                        member["sentence"],
                        digest,
                        rows,
                        columns,
                    )
                )
            return dataset_outcome(members)

        self._step({"op": "fetch", "ref": ref}, run)

    def mint(self, spec: Spec) -> str:
        operation: list[dict[str, Any]] = []

        def run() -> dict[str, Any]:
            response = self.recorder.api("POST", "/datasets", _build_body(spec, None))
            self._raise_problem(response)
            require(
                response.status == 202, f"the build answered {response.status}, not 202"
            )
            operation.append(response.json()["operation"])
            return {
                "operation": {
                    "operation_id": operation[0]["operation_id"],
                    "status": operation[0]["status"],
                }
            }

        self._step({"op": "mint", "spec": spec.step()}, run)
        self.minted = (spec, _reached(operation, "the mint"))
        return self.minted[1]["operation_id"]

    def result(self) -> None:
        """Waits for the operation the last mint started."""
        if self.minted is None:
            raise ValueError("result follows mint")
        spec, operation = self.minted

        def run() -> dict[str, Any]:
            manifest = self._wait(operation)
            return dataset_outcome([self._member([], spec, manifest)])

        self._step({"op": "result"}, run)

    # -- building --

    def build(
        self, parts: list[str], spec: Spec, scope: dict[str, Any] | None
    ) -> Member:
        """Builds one member: the build, the sentence when the client lacks it,
        then the file."""
        response = self.recorder.api("POST", "/datasets", _build_body(spec, scope))
        self._raise_problem(response)
        body = response.json()
        manifest = self._wait(body["operation"]) if response.status == 202 else body
        return self._member(parts, spec, manifest)

    def _member(self, parts: list[str], spec: Spec, manifest: dict[str, Any]) -> Member:
        definition = manifest["definition"]
        sentence = spec.sentence
        if definition["spec_id"] != spec.spec_id or sentence is None:
            sentence = self._identity_sentence(parts, definition)
        artifact = next(
            a
            for a in manifest["artifacts"]
            if a["rendering"].get("format") == "parquet"
            and a["rendering"].get("bundle") is False
            and a["rendering"].get("layout") != "frame"
        )
        rows, columns = self.read_shape(self._download(artifact))
        return Member(
            parts,
            definition,
            manifest["content_version"],
            sentence,
            artifact["artifact_digest"],
            rows,
            columns,
        )

    def _identity_sentence(self, parts: list[str], definition: dict[str, Any]) -> str:
        entry = {
            "entry_kind": "identity",
            "variable_text": parts[0] if parts else UNNAMED_VARIABLE_TEXT,
            "identity": definition["identity"],
        }
        answer = self._resolve({"kind": "dataset_resubmit", "entries": [entry]})
        (part,) = answer.parts
        require(part["status"] == "definite", "the identity resubmit is definite")
        require(
            part["spec"]["spec_id"] == definition["spec_id"],
            "the resubmit keeps the id",
        )
        return part["spec"]["resolved"]["rendered_sentence"]

    def _download(self, artifact: dict[str, Any]) -> bytes:
        files = self.recorder.origin("GET", artifact["url"])
        self._raise_problem(files)
        require(files.status == 302, f"the files route answered {files.status}")
        data = self.recorder.external(files.headers["location"])
        require(data.status == 200, f"the signed download answered {data.status}")
        digest = "sha256:" + hashlib.sha256(data.body).hexdigest()
        require(
            digest == artifact["artifact_digest"], "the download matches its digest"
        )
        return data.body

    def _wait(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Polls at the server's pacing, else backs off from 1 to 8 seconds."""
        backoff = 1.0
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if "retry_after_ms" in operation:
                self.sleep(operation["retry_after_ms"] / 1000)
            else:
                self.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
            path = POLL_PATH.format(operation_id=operation["operation_id"])
            response = self.recorder.api("GET", path)
            self._raise_problem(response)
            resource = response.json()
            operation = resource["operation"]
            if operation["status"] == "done":
                return resource["manifest"]
        raise SystemExit(f"{operation['operation_id']} did not finish in 30 minutes")

    def _raise_problem(self, response: Response) -> None:
        if response.status < 400:
            return
        raise refusal_error(response.status, response.json(), self.actions)


def _window(window: str | tuple[int, int]) -> dict[str, Any]:
    if isinstance(window, tuple):
        return {"kind": "range", "start_year": window[0], "end_year": window[1]}
    return {"kind": window}


def _build_body(spec: Spec, scope: dict[str, Any] | None) -> dict[str, Any]:
    input_: dict[str, Any] = {
        "kind": "spec_id",
        "spec_id": spec.spec_id,
        "definition": spec.definition,
    }
    if scope:
        input_["scope"] = scope
    return {"schema_version": 1, "input": input_, "options": dict(RENDERING)}


def with_place_options(body: Any) -> Any:
    """`body` with each no_data `data_query` replaced by the `options` a
    choice refusal carries once Search serves them: per ambiguous wording and
    candidate, the data_query with that candidate pinned on every row that
    has the wording."""
    body = copy.deepcopy(body)
    for part in body.get("parts", []) if isinstance(body, dict) else []:
        no_data = part.get("no_data")
        if not no_data or "data_query" not in no_data:
            continue
        base = no_data.pop("data_query")
        options = []
        for group in no_data.get("ambiguous_entities", []):
            for candidate in group["candidates"]:
                query = copy.deepcopy(base)
                for row in query["parts"][0]["entities"]:
                    if row["text"] == group["text"]:
                        row["dcid"] = candidate["ref"]
                options.append(
                    {
                        "mentions": [group["text"]],
                        "label": candidate["label"],
                        "data_query": query,
                    }
                )
        no_data["options"] = options
    return body


# --- scenarios --------------------------------------------------------------

DEFINITE = "ODA disbursements from Germany to Nigeria for basic health care"
CANDIDATES = "GNI of Germany from 2015 to 2022"
MIXED = "debt service of Kenya and the price of tea in Kenya"
FOLDED = "ODA disbursements for health and for education from Germany to Kenya, and GDP of Kenya"
PLACE = "ODA disbursements from Germany to Congo"
TWO_PLACES = "Kenya's GDP, and ODA disbursements from Germany to Congo"
DECLINED = "should donors give more aid to Kenya?"
CATALOGUE = "what data do you have?"
TOP5 = "top 5 donors to Ethiopia in 2022"
CANDIDATES_BESIDE_GROUP = (
    "GNI of Germany from 2015 to 2022, and ODA disbursements from Germany to "
    "sub-Saharan Africa"
)
TWO_CANDIDATES = "GNI of Germany and tax revenue of Kenya from 2015 to 2022"
CANDIDATES_BESIDE_UNRESTATED = "GNI of Germany and debt of Kenya from 2015 to 2022"
GROUP_DEFINITE = "GDP of the least developed countries"
ASYNC = "ODA received by every recipient country from every provider (DAC2a) from 2018 to 2024"
NARROW_WINDOW = (2020, 2023)
SYNC_ROWS = 100_000


@dataclass
class Context:
    """Prerequisites a scenario needs but does not record, fetched once."""

    base_url: str
    transport: Transport
    actions: Mapping[str, Mapping[str, Any]]
    client_codes: Mapping[str, Mapping[str, Any]]
    read_shape: Callable[[bytes], tuple[int, list[str]]] = parquet_shape
    _cache: dict[str, Any] = field(default_factory=dict)

    def session(self, name: str, **recorder: Any) -> Session:
        return Session(
            Recorder(name, self.base_url, self.transport, **recorder),
            self.actions,
            self.client_codes,
            read_shape=self.read_shape,
        )

    def raised(self, code: str) -> dict[str, Any]:
        """The outcome of an error the client raises with `code`."""
        return ClientError(code).outcome(self.client_codes)

    def definite_answer(self, question: str) -> Answer:
        if question not in self._cache:
            answer = self.session("prerequisite").search(question)
            require(answer.status == "definite", f"{question!r} is definite")
            self._cache[question] = answer
        return self._cache[question]

    def definite_spec(self, question: str) -> Spec:
        (part,) = self.definite_answer(question).parts
        return _part_spec(part["spec"])

    def narrowed_spec(self) -> Spec:
        """The definite spec narrowed to 2020 to 2023, with the sentence its build has."""
        if "narrowed" not in self._cache:
            member = self.session("prerequisite").fetch_spec(
                self.definite_spec(DEFINITE), window=NARROW_WINDOW
            )[0]
            self._cache["narrowed"] = Spec(
                member.spec_id, member.definition, member.sentence
            )
        return self._cache["narrowed"]

    def definite_ref(self) -> dict[str, Any]:
        if "ref" not in self._cache:
            answer = self.definite_answer(DEFINITE)
            session = self.session("prerequisite")
            ((parts, spec),) = _members(answer)
            member = session.build(parts, spec, None)
            self._cache["ref"] = {"format_version": 1, "members": [member.ref()]}
        return self._cache["ref"]


@dataclass(frozen=True)
class Recorded:
    name: str
    description: str
    run: Callable[[Session, Context], None]


def _answer_definite_fetch(s: Session, ctx: Context) -> None:
    answer = s.search(DEFINITE)
    require(answer.status == "definite" and len(answer.parts) == 1, "one definite part")
    s.fetch_answer(answer)


def _answer_candidates(s: Session, ctx: Context) -> None:
    answer = s.search(CANDIDATES)
    require(
        answer.status == "candidates" and len(answer.parts) == 1, "one candidates part"
    )
    s.fetch_answer(s.choose(answer, 0))


def _answer_mixed_raises(s: Session, ctx: Context) -> None:
    answer = s.search(MIXED)
    require(answer.status == "mixed", "a mixed answer")
    require(
        [p["status"] for p in answer.parts] == ["definite", "no_data"],
        "definite, no_data",
    )
    s.fetch_answer(answer)


def _answer_mixed_partial(s: Session, ctx: Context) -> None:
    answer = s.search(MIXED)
    require(
        [p["status"] for p in answer.parts] == ["definite", "no_data"],
        "definite, no_data",
    )
    s.fetch_answer(answer, partial=True)


def _answer_folded(s: Session, ctx: Context) -> None:
    answer = s.search(FOLDED)
    ids = [p.get("spec", {}).get("spec_id") for p in answer.parts]
    require(answer.status == "definite" and len(ids) == 3, "three definite parts")
    require(ids[0] == ids[1] != ids[2], "the first two parts fold into one spec")
    require(
        any(w["code"] == "binding_folded" for w in answer.parts[1]["warnings"]),
        "the second part carries binding_folded",
    )
    s.fetch_answer(answer)


def _fetch_spec_narrowed(s: Session, ctx: Context) -> None:
    spec = ctx.definite_spec(DEFINITE)
    (member,) = s.fetch_spec(spec, window=NARROW_WINDOW)
    require(member.spec_id != spec.spec_id, "the window gives a new spec_id")
    require("2020 to 2023" in member.sentence, "the sentence names the window")


def _fetch_spec_known_sentence(s: Session, ctx: Context) -> None:
    spec = ctx.narrowed_spec()
    (member,) = s.fetch_spec(spec)
    require(member.spec_id == spec.spec_id, "the build keeps the spec_id")


def _fetch_spec_request_invalid(s: Session, ctx: Context) -> None:
    s.fetch_spec(ctx.definite_spec(DEFINITE), facets={"colour": "blue"})
    require(
        s.expect[-1].get("error", {}).get("code") == "request_invalid",
        "request_invalid",
    )


def _fetch_ref(s: Session, ctx: Context) -> None:
    s.fetch_ref(ctx.definite_ref())
    require("dataset" in s.expect[-1], "the ref's version is fetched")


def _fetch_ref_unknown_version(s: Session, ctx: Context) -> None:
    ref = copy.deepcopy(ctx.definite_ref())
    ref["members"][0]["content_version"] = "cv_" + "0" * 32
    s.fetch_ref(ref)
    require(
        s.expect[-1].get("error", {}).get("code") == "snapshot_not_found",
        "snapshot_not_found",
    )


def _mint_async(s: Session, ctx: Context) -> None:
    (part,) = ctx.definite_answer(ASYNC).parts
    rows = part["spec"]["resolved"].get("download", {}).get("rows") or 0
    require(
        SYNC_ROWS < rows < 2 * SYNC_ROWS, f"{rows} rows is just above the sync budget"
    )
    s.mint(_part_spec(part["spec"]))
    s.result()


def _answer_kind(question: str, kind: str) -> Callable[[Session, Context], None]:
    def run(s: Session, ctx: Context) -> None:
        answer = s.search(question)
        require(answer.body["kind"] == kind, f"a {kind} answer")
        s.fetch_answer(answer)

    return run


def _search_place(question: str, parts: int) -> Callable[[Session, Context], None]:
    def run(s: Session, ctx: Context) -> None:
        answer = s.search(question)
        require(
            [p.get("no_data", {}).get("reason") for p in answer.parts]
            == ["entity_ambiguous"] * parts,
            f"{parts} entity_ambiguous part(s)",
        )

    return run


def _statuses(answer: Answer) -> list[str]:
    return [part.get("status") for part in answer.parts]


def _search_warning_top5(s: Session, ctx: Context) -> None:
    answer = s.search(TOP5)
    require(_statuses(answer) == ["definite"], "one definite part")
    operations = answer.parts[0].get("operations") or []
    require(any(o.get("verb") == "rank" for o in operations), "the rank is not done")
    s.fetch_answer(answer, strict=True)
    s.fetch_answer(answer)


def _choose_candidate_two_parts(s: Session, ctx: Context) -> None:
    answer = s.search(CANDIDATES_BESIDE_GROUP)
    require(_statuses(answer) == ["candidates", "definite"], "candidates, definite")
    require(
        bool(answer.parts[1]["spec"]["resolved"].get("entity_groups")),
        "the definite part names an entity group",
    )
    chosen = s.choose(answer, 0, part=0)
    require(
        chosen is not None and _statuses(chosen) == ["definite", "definite"],
        "the choice answers both parts definite",
    )
    if chosen is not None:
        s.fetch_answer(chosen)


def _resubmit_candidates_sibling(s: Session, ctx: Context) -> None:
    answer = s.search(TWO_CANDIDATES)
    require(_statuses(answer) == ["candidates", "candidates"], "two candidates parts")
    chosen = s.choose(answer, 0, part=0)
    require(
        chosen is not None and _statuses(chosen) == ["definite", "candidates"],
        "the choice settles part 0 and leaves part 1's candidates",
    )
    if chosen is not None:
        before = [c["spec_id"] for c in answer.parts[1]["candidates"]["specs"]]
        after = [c["spec_id"] for c in chosen.parts[1]["candidates"]["specs"]]
        require(before == after, "the candidates keep their posted order")


def _resubmit_placeholder(s: Session, ctx: Context) -> None:
    answer = s.search(CANDIDATES_BESIDE_UNRESTATED)
    require(_statuses(answer) == ["candidates", "no_data"], "candidates, no_data")
    require("restatement" not in answer.parts[1], "part 1 has no restatement")
    chosen = s.choose(answer, 0, part=0)
    require(
        chosen is not None and _statuses(chosen)[0] == "definite",
        "the choice settles part 0",
    )


RECORDED = (
    Recorded(
        "answer-definite-fetch",
        "A question with one reading answers definite, and fetching the answer builds the "
        "part's spec as bare Parquet, follows the files route's redirect with a bare request, "
        "and returns one member whose parts hold the part's variable text and whose sentence "
        "is the part's spec.resolved.rendered_sentence, with no Search call after the first.",
        _answer_definite_fetch,
    ),
    Recorded(
        "answer-candidates-choose-fetch",
        "A question with three readings answers one candidates part, a choice gap with three "
        "options. Choosing option 0 makes no request: the new answer is definite, its sentence "
        "is that candidate's spec.resolved.rendered_sentence, and fetching it builds that "
        "candidate's spec.",
        _answer_candidates,
    ),
    Recorded(
        "answer-mixed-fetch-raises",
        "A question with one answered part and one part Search cannot read answers mixed, "
        "incomplete, with a not_understood gap on part 1. Fetching it raises "
        "IncompleteAnswerError before any build.",
        _answer_mixed_raises,
    ),
    Recorded(
        "answer-mixed-fetch-partial",
        "Fetching the mixed answer with partial=True builds only the answered part and returns "
        "a dataset that is not complete, with part 1's not_understood gap in missing.",
        _answer_mixed_partial,
    ),
    Recorded(
        "answer-several-readings-folded",
        "A question with three definite parts, the first two folded into one spec_id "
        "(binding_folded), builds each distinct spec_id once, in part order, each through build, "
        "files and download before the next. The dataset stacks two members, the first answering "
        "both folded parts, into one table with a part column first and each member's columns in "
        "member order.",
        _answer_folded,
    ),
    Recorded(
        "fetch-spec-narrowed",
        "Fetching a spec with window=(2020, 2023) posts the window as input.scope, and the build "
        "answers a new spec_id. The client stores the built spec_id and content_version, takes "
        "the sentence from Search by posting the built definition's identity as a dataset_resubmit "
        'identity entry (variable_text "dataset" for a spec no answer part names), checks the '
        "answer's spec_id equals the built one, then downloads the file.",
        _fetch_spec_narrowed,
    ),
    Recorded(
        "fetch-spec-known-sentence",
        "Fetching a spec whose sentence the client holds builds bare Parquet, follows the files "
        "route's redirect with a bare request, and returns one member with that sentence and no "
        "Search call.",
        _fetch_spec_known_sentence,
    ),
    Recorded(
        "fetch-spec-request-invalid",
        "Fetching a spec with a facet the server does not know sends it in input.scope.facets; "
        "the build refuses 400 request_invalid, which raises InvalidRequestError.",
        _fetch_spec_request_invalid,
    ),
    Recorded(
        "fetch-ref",
        "Fetching a ref with the cache off reads the stored version through the snapshot route, "
        "then the files route at the artifact url whose digest the ref stores, and returns the "
        "ref's member with its stored parts and sentence and no build or Search call.",
        _fetch_ref,
    ),
    Recorded(
        "fetch-ref-unknown-version",
        "Fetching a ref whose content_version the server never built ends at the snapshot "
        "route's 404 snapshot_not_found, which raises NotFoundError with no build: an unknown "
        "version is not an expired one.",
        _fetch_ref_unknown_version,
    ),
    Recorded(
        "mint-async-build",
        "Minting a spec whose estimate is just above the 100,000-row sync budget answers 202 "
        "with an operation the client reports as pending. Waiting polls the operation path "
        "built from operation_id, sleeping retry_after_ms when the response carries it and "
        "backing off from 1 to 8 seconds otherwise, until done; the done manifest's file is "
        "then downloaded.",
        _mint_async,
    ),
    Recorded(
        "answer-declined",
        "A question that asks for analysis answers declined, with no parts and no sentence, "
        "and is not complete. Fetching it raises DeclinedError with no request.",
        _answer_kind(DECLINED, "declined"),
    ),
    Recorded(
        "answer-catalogue",
        "A question about what data exists answers catalogue, with no parts and no sentence, "
        "and is not complete. Fetching it raises DeclinedError with no request.",
        _answer_kind(CATALOGUE, "catalogue"),
    ),
    Recorded(
        "search-place-ambiguous",
        "A question naming a place that matches two countries answers with one no_data part "
        "whose entity_ambiguous reason makes a choice gap. This build carries no options, so "
        "the gap offers none.",
        _search_place(PLACE, 1),
    ),
    Recorded(
        "search-two-parts-place-ambiguous",
        "A two-part question naming a place that matches two countries answers no_data on both "
        "parts, each an entity_ambiguous choice gap. This build carries no options, so the gaps "
        "offer none.",
        _search_place(TWO_PLACES, 2),
    ),
    Recorded(
        "search-warning-top5",
        "A question asking for a ranking answers one definite part that does not run the rank, "
        "with a departure and two warn-severity warnings. A strict fetch raises SearchWarning "
        "before any build; a fetch that is not strict builds the dataset and issues one "
        "SearchWarning for all of them.",
        _search_warning_top5,
    ),
    Recorded(
        "choose-candidate-two-parts",
        "Choosing candidate 0 on the candidates part of a two-part answer posts dataset_resubmit "
        "with one entry per part in part order: the chosen candidate's identity, and the definite "
        "part's identity with its entity_groups. Both parts answer definite, and the fetch stacks "
        "two members with each member's parts as its part column value.",
        _choose_candidate_two_parts,
    ),
    Recorded(
        "resubmit-candidates-sibling",
        "Choosing candidate 0 on part 0 of an answer with two candidates parts posts part 1 as a "
        "candidates entry: its candidate set in the server's order, each with its identity, and "
        "its ordering and truncated echoed. Part 1 answers candidates again in the same order.",
        _resubmit_candidates_sibling,
    ),
    Recorded(
        "resubmit-placeholder-without-restatement",
        "Choosing candidate 0 on part 0 posts part 1, a no_data part with no restatement, as a "
        "placeholder entry with its variable_text, status and reason.",
        _resubmit_placeholder,
    ),
)


# --- synthetic scenarios ----------------------------------------------------


def _problem(
    code: str, status: int, title: str, detail: str, *, retryable: bool
) -> dict[str, Any]:
    return {
        "type": f"/codes/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "trace_id": "0" * 32,
        "capability_version": "synthetic",
        "retryable": retryable,
    }


def _refusal(
    request: dict[str, Any],
    status: int,
    body: dict[str, Any],
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """An exchange answering `request` with a refusal: a problem document, or
    the website proxy's own `{error, retry, detail}` JSON."""
    media = "application/json" if "error" in body else "application/problem+json"
    return {
        "request": copy.deepcopy(request),
        "response": {
            "status": status,
            "headers": {"content-type": media, **(headers or {})},
            "json": body,
        },
    }


_PROXY_RATE_LIMITED = {
    "error": "rate_limited",
    "retry": "retry_after_delay",
    "detail": "Too many requests from this address. Try again shortly.",
}
_PROXY_UNAVAILABLE = {
    "error": "transient_unavailable",
    "retry": "retry_after_delay",
    "detail": "The Search API is unreachable.",
}


@dataclass(frozen=True)
class Derived:
    call: list[dict[str, Any]]
    exchanges: list[dict[str, Any]]
    expect: list[dict[str, Any]]


@dataclass(frozen=True)
class Synthetic:
    """A scenario derived from a recorded one. A `live` derivation records
    some of its exchanges against the base URL."""

    name: str
    derived_from: str
    edit: str
    description: str
    derive: Callable[[dict[str, Any], Context], Derived]
    live: bool = False


def _same(parent: dict[str, Any], exchanges: list[dict[str, Any]]) -> Derived:
    return Derived(
        copy.deepcopy(parent["call"]), exchanges, copy.deepcopy(parent["expect"])
    )


def _retry_after_503(parent: dict[str, Any], ctx: Context) -> Derived:
    first = parent["exchanges"][0]
    problem = _problem(
        "service_unavailable",
        503,
        "Service Unavailable",
        "The service is busy.",
        retryable=True,
    )
    refused = _refusal(first["request"], 503, problem, {"retry-after": "1"})
    return _same(parent, [refused, *copy.deepcopy(parent["exchanges"])])


def _proxy_429(parent: dict[str, Any], ctx: Context) -> Derived:
    first = parent["exchanges"][0]
    refused = _refusal(
        first["request"], 429, dict(_PROXY_RATE_LIMITED), {"retry-after": "2"}
    )
    return _same(parent, [refused, *copy.deepcopy(parent["exchanges"])])


def _proxy_502(parent: dict[str, Any], ctx: Context) -> Derived:
    first = parent["exchanges"][0]
    refused = [
        _refusal(first["request"], 502, dict(_PROXY_UNAVAILABLE)) for _ in range(3)
    ]
    error = refusal_error(502, _PROXY_UNAVAILABLE, ctx.actions).outcome(
        ctx.client_codes
    )
    return Derived(copy.deepcopy(parent["call"]), refused, [error])


def _build_index(parent: dict[str, Any]) -> int:
    return next(
        i
        for i, e in enumerate(parent["exchanges"])
        if e["request"]["method"] == "POST" and e["request"].get("path") == "/datasets"
    )


def _retry_after_504(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    index = _build_index(parent)
    problem = _problem(
        "timeout",
        504,
        "Gateway Timeout",
        "The build passed its 120 s limit.",
        retryable=True,
    )
    exchanges.insert(index, _refusal(exchanges[index]["request"], 504, problem))
    return _same(parent, exchanges)


def _signed_url_refused(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    index = next(i for i, e in enumerate(exchanges) if "url" in e["request"])
    files, signed = exchanges[index - 1], exchanges[index]
    refused = {
        "request": copy.deepcopy(signed["request"]),
        "response": {"status": 403, "headers": {"content-type": "application/xml"}},
    }
    exchanges[index:index] = [refused, copy.deepcopy(files)]
    return _same(parent, exchanges)


def _deferred(body: dict[str, Any]) -> None:
    body["parts"][1]["status"] = "deferred"


def _unknown_enum_value(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    exchanges[0]["response"]["json"]["parts"][0]["no_data"]["category"] = "undetermined"
    return _same(parent, exchanges)


def _null_member(parent: dict[str, Any], ctx: Context) -> Derived:
    """Flips the status column's `distinct`, a required member that admits
    null, between left out and served as null."""
    exchanges = copy.deepcopy(parent["exchanges"])
    manifest = exchanges[_build_index(parent)]["response"]["json"]
    column = next(
        c for c in manifest["output_options"]["columns"] if c["kind"] == "status"
    )
    if "distinct" in column:
        require(column["distinct"] is None, "the status column's distinct is null")
        del column["distinct"]
    else:
        column["distinct"] = None
    return _same(parent, exchanges)


def _renamed_strict_field(parent: dict[str, Any], ctx: Context) -> Derived:
    index = _build_index(parent)
    exchanges = copy.deepcopy(parent["exchanges"][: index + 1])
    manifest = exchanges[index]["response"]["json"]
    manifest["contentVersion"] = manifest.pop("content_version")
    error = ctx.raised("contract_violation")
    return Derived(
        copy.deepcopy(parent["call"]), exchanges, [parent["expect"][0], error]
    )


type BodyEdit = Callable[[Any, Context], Any]


def _with_options(body: Any, ctx: Context) -> Any:
    return with_place_options(body)


def _choice(
    option: int | str,
    part: int | str | None = None,
    *,
    edit: BodyEdit | None = None,
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's search, its answer body changed by `edit`, then a choice.
    A choice that posts is recorded live, its answer changed by
    `with_place_options`; one that refuses makes no request."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        canned = copy.deepcopy(parent["exchanges"][:1])
        if edit is not None:
            response = canned[0]["response"]
            response["json"] = edit(response["json"], ctx)
        session = ctx.session("synthetic", canned=canned, edit=with_place_options)
        answer = session.search(parent["call"][0]["question"])
        session.choose(answer, option, part=part)
        return Derived(session.call, session.recorder.exchanges, session.expect)

    return derive


def _definite_beside(body: Any, ctx: Context) -> Any:
    """Part 0 becomes the definite part a live search for GROUP_DEFINITE
    answers, and the other part's data_query becomes options."""
    body = with_place_options(body)
    (part,) = copy.deepcopy(ctx.definite_answer(GROUP_DEFINITE).parts)
    require(bool(part["spec"]["resolved"].get("entity_groups")), "a group part")
    body["parts"][0] = part
    body["status"] = "mixed"
    return body


def _choice_refused(
    option: int | str, part: int | str | None = None, *, options: bool = False
) -> Callable[[dict[str, Any], Context], Derived]:
    choose = _choice(option, part, edit=_with_options if options else None)

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        derived = choose(parent, ctx)
        require(
            derived.expect[-1] == ctx.raised("choice_invalid"),
            "the choice refuses",
        )
        return derived

    return derive


def _answer_edit(
    change: Callable[[dict[str, Any]], None],
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent with its first answer body changed, and that answer's
    outcome derived again."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        exchanges = copy.deepcopy(parent["exchanges"])
        body = exchanges[0]["response"]["json"]
        change(body)
        expect = copy.deepcopy(parent["expect"])
        expect[0] = answer_outcome(answer_from_body(body), gap_kinds(ctx.actions))
        return Derived(copy.deepcopy(parent["call"]), exchanges, expect)

    return derive


def _timed_out(body: dict[str, Any]) -> None:
    body["diagnostics"]["timed_out"] = True


def _unsupported(body: dict[str, Any]) -> None:
    part = body["parts"][1]
    del part["no_data"]
    part["status"] = "unsupported"
    part["unsupported"] = {
        "code": "scope_too_broad",
        "pointer": "/definition/identity",
        "detail": "the family is too broad to settle; bind a what or how slot",
    }


def _unknown_kind(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    exchanges[0]["response"]["json"]["kind"] = "verdict"
    return Derived(
        copy.deepcopy(parent["call"]), exchanges, [ctx.raised("contract_violation")]
    )


def _partial_nothing_answered(parent: dict[str, Any], ctx: Context) -> Derived:
    require(parent["expect"][0]["answer"]["specs"] == [], "no part has a reading")
    return Derived(
        [*copy.deepcopy(parent["call"]), {"op": "fetch", "partial": True}],
        copy.deepcopy(parent["exchanges"]),
        [*copy.deepcopy(parent["expect"]), ctx.raised("answer_incomplete")],
    )


_INFO_NOTICE = {
    "code": "retrieval_score_weak",
    "severity": "info",
    "message": (
        "the chosen reading's match to the query is weak; treat it with less "
        "confidence than a strong match."
    ),
}


def _strict_info_notice(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    exchanges[0]["response"]["json"]["parts"][0]["warnings"].append(dict(_INFO_NOTICE))
    call = copy.deepcopy(parent["call"])
    call[1]["strict"] = True
    require(
        parent["expect"][1]["dataset"]["search_warning"] is False,
        "the parent's fetch issues no SearchWarning",
    )
    return Derived(call, exchanges, copy.deepcopy(parent["expect"]))


SYNTHETIC = (
    Synthetic(
        "search-retry-after-503",
        "search-place-ambiguous",
        "A 503 service_unavailable with retryable true and Retry-After 1 answers the first search.",
        "A retryable 503 problem with Retry-After is retried after the wait it names, and the "
        "search then reaches the same answer; the retry spends one of the two default retries.",
        _retry_after_503,
    ),
    Synthetic(
        "search-proxy-rate-limited",
        "search-place-ambiguous",
        "The website proxy's own 429 {error, retry, detail} with Retry-After 2 answers the first search.",
        "The proxy's 429 body carries no code or retryable: error is the code and retry "
        '"retry_after_delay" makes it retryable, so the client waits the Retry-After seconds, '
        "retries, and reaches the same answer.",
        _proxy_429,
    ),
    Synthetic(
        "search-proxy-unavailable",
        "search-place-ambiguous",
        "The website proxy's own 502 {error, retry, detail} answers the search and both retries.",
        "The proxy's 502 body is retryable through its retry member and has no Retry-After, so "
        "the client backs off and retries twice, then raises ServerError with the error member, "
        "transient_unavailable, as its code.",
        _proxy_502,
    ),
    Synthetic(
        "fetch-retry-after-504",
        "answer-definite-fetch",
        "A 504 timeout with retryable true answers the first build.",
        "A synchronous build cut off at the server's limit answers a retryable 504 timeout; the "
        "client builds once more and reaches the same dataset.",
        _retry_after_504,
    ),
    Synthetic(
        "fetch-ref-signed-url-refused",
        "fetch-ref",
        "The signed download answers 403 once, and the files route is asked again.",
        "A signed storage URL that refuses is never retried: the client asks the files route "
        "for a fresh redirect and downloads from that, reaching the same dataset.",
        _signed_url_refused,
    ),
    Synthetic(
        "answer-unknown-part-status",
        "answer-mixed-fetch-raises",
        'Part 1\'s status becomes "deferred", a status no client knows.',
        "A part status the client does not know parses as an unknown part, counts as an "
        "unknown gap with no options, and makes fetching the answer raise "
        "IncompleteAnswerError.",
        _answer_edit(_deferred),
    ),
    Synthetic(
        "answer-unknown-enum-value",
        "search-place-ambiguous",
        'The no_data part\'s category becomes "undetermined", a value the served enum lacks.',
        "An enum value the client does not know passes through untouched and changes no "
        "outcome.",
        _unknown_enum_value,
    ),
    Synthetic(
        "fetch-null-member",
        "answer-definite-fetch",
        "The manifest's status column distinct flips between left out and served as null.",
        "A required member that admits null may be left out or served as null; a client reads "
        "both as null, and neither changes the outcome. The recording and this record carry "
        "the two forms of the status column's distinct.",
        _null_member,
    ),
    Synthetic(
        "fetch-renamed-strict-field",
        "answer-definite-fetch",
        "The build's manifest names content_version contentVersion, and the exchanges after it go.",
        "A manifest without content_version, a strict field, raises ContractError on the build "
        "response, before any download.",
        _renamed_strict_field,
    ),
    Synthetic(
        "choose-place-one-part",
        "search-place-ambiguous",
        "Each refusal's data_query becomes the no_data options Search serves, and the chosen option's answer is recorded live.",
        "A place refusal offers one option per candidate, so its choice gap has two options. "
        'Choosing "Congo [DRC]" by label in a one-part answer posts that option\'s data_query '
        "unchanged as the input of a new search, which answers definite.",
        _choice("Congo [DRC]", edit=_with_options),
        live=True,
    ),
    Synthetic(
        "choose-place-two-parts",
        "search-two-parts-place-ambiguous",
        "Each refusal's data_query becomes the no_data options Search serves, and the chosen option's answer is recorded live.",
        'Choosing "Congo [DRC]" on part 1 of a two-part answer posts dataset_resubmit with one '
        "entry per part in part order: the chosen option's data_query part as a parsed entry, "
        "and part 0, still a refusal, as a parsed entry of its restatement. Part 1 answers "
        "definite and part 0 refuses again with its two options.",
        _choice("Congo [DRC]", 1, edit=_with_options),
        live=True,
    ),
    Synthetic(
        "choose-place-beside-definite",
        "search-two-parts-place-ambiguous",
        f"Part 0 becomes the definite part, entity groups included, of a live search for "
        f'"{GROUP_DEFINITE}"; part 1\'s data_query becomes options; the choice is recorded live.',
        'Choosing "Congo [DRC]" on part 1 beside a definite part posts dataset_resubmit with the '
        "definite part's identity and entity_groups as an identity entry, and the chosen "
        "option's data_query part as a parsed entry. Both parts answer definite.",
        _choice("Congo [DRC]", 1, edit=_definite_beside),
        live=True,
    ),
    Synthetic(
        "choose-part-by-mention",
        "search-place-ambiguous",
        "The refusal's data_query becomes options, and the choice is recorded live.",
        'Naming the part by "Congo", a word its options mention, and the option by label posts '
        "that option's data_query, as naming the part by index does.",
        _choice("Congo [DRC]", "Congo", edit=_with_options),
        live=True,
    ),
    Synthetic(
        "answer-timed-out",
        "answer-mixed-fetch-raises",
        "The answer's diagnostics.timed_out becomes true.",
        "A no_data part of an answer whose diagnostics say Search timed out is a timed_out gap, "
        "whatever its reason, and fetching the answer raises IncompleteAnswerError.",
        _answer_edit(_timed_out),
    ),
    Synthetic(
        "answer-unsupported-part",
        "answer-mixed-fetch-raises",
        "Part 1 becomes an unsupported part with code scope_too_broad.",
        "An unsupported part is an unsupported gap with no options, and fetching the answer "
        "raises IncompleteAnswerError.",
        _answer_edit(_unsupported),
    ),
    Synthetic(
        "answer-unknown-kind",
        "search-place-ambiguous",
        'The answer\'s kind becomes "verdict", a kind no client knows.',
        "A search response whose kind the client does not know raises ContractError, since the "
        "client cannot read the answer.",
        _unknown_kind,
    ),
    Synthetic(
        "fetch-partial-nothing-answered",
        "search-place-ambiguous",
        "A partial fetch of the answer follows the search.",
        "A partial fetch of an answer in which no part has a reading raises "
        "IncompleteAnswerError with no request.",
        _partial_nothing_answered,
    ),
    Synthetic(
        "fetch-strict-info-notice",
        "answer-definite-fetch",
        "Part 0 carries an info-severity warning, and the fetch is strict.",
        "An info-severity warning issues no SearchWarning, so a strict fetch of the answer "
        "builds the same dataset.",
        _strict_info_notice,
    ),
    Synthetic(
        "choose-refused-no-choice",
        "answer-definite-fetch",
        "A choice of option 0 follows the search.",
        "Choosing on an answer in which no part awaits a choice raises InvalidRequestError "
        "with no request.",
        _choice_refused(0),
    ),
    Synthetic(
        "choose-refused-two-choices",
        "search-two-parts-place-ambiguous",
        "A choice of option 0, naming no part, follows the search.",
        "Choosing without naming a part when two parts await a choice raises "
        "InvalidRequestError with no request.",
        _choice_refused(0),
    ),
    Synthetic(
        "choose-refused-no-options",
        "search-place-ambiguous",
        "A choice of option 0 follows the search.",
        "Choosing in a choice gap that offers no options raises InvalidRequestError with no "
        "request.",
        _choice_refused(0),
    ),
    Synthetic(
        "choose-refused-option-out-of-range",
        "answer-candidates-choose-fetch",
        "A choice of candidate 3 follows the search.",
        "Choosing candidate 3 of three raises InvalidRequestError with no request.",
        _choice_refused(3),
    ),
    Synthetic(
        "choose-refused-unknown-label",
        "search-place-ambiguous",
        'The refusal\'s data_query becomes options, and a choice of "Congo [Nowhere]" follows.',
        "Choosing an option by a label no option has raises InvalidRequestError with no "
        "request.",
        _choice_refused("Congo [Nowhere]", options=True),
    ),
    Synthetic(
        "choose-refused-answered-part",
        "answer-mixed-fetch-raises",
        "A choice of option 0 on part 0, which has a reading, follows the search.",
        "Choosing on a part that already has a reading raises InvalidRequestError with no "
        "request.",
        _choice_refused(0, 0),
    ),
    Synthetic(
        "choose-refused-unknown-part",
        "answer-mixed-fetch-raises",
        'A choice of option 0 on part "rainfall" follows the search.',
        "Naming a part by words that are neither a part's variable text nor a word its "
        "options mention raises InvalidRequestError with no request.",
        _choice_refused(0, "rainfall"),
    ),
    Synthetic(
        "choose-refused-mention-names-two-parts",
        "search-two-parts-place-ambiguous",
        'Each refusal\'s data_query becomes options, and a choice on part "Congo" follows.',
        'Naming a part by "Congo", a word both parts\' options mention, raises '
        "InvalidRequestError with no request.",
        _choice_refused("Congo [DRC]", "Congo", options=True),
    ),
)


# --- writing and running ----------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
    name: str, description: str, origin: dict[str, Any], derived: Derived
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "name": name,
        "description": description,
        "origin": origin,
        "call": derived.call,
        "exchanges": derived.exchanges,
        "expect": derived.expect,
    }


def write_record(
    directory: Path, record: dict[str, Any], bodies: Mapping[str, bytes]
) -> None:
    """Writes `record` and its bodies, removing the body files of its own an
    earlier record of the same name named. A parent's body file it names
    stays."""
    path = directory / f"{record['name']}.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        for exchange in old["exchanges"]:
            body_file = exchange["response"].get("body_file")
            if body_file and owns_body_file(record["name"], body_file):
                (directory / body_file).unlink(missing_ok=True)
    for body_file, data in bodies.items():
        (directory / body_file).write_bytes(data)
    _write_json(path, record)


def record_recorded(definition: Recorded, ctx: Context, directory: Path) -> None:
    recorded_at = _now()
    session = ctx.session(definition.name)
    definition.run(session, ctx)
    commits = session.recorder.commits
    if len(commits) != 1 or not re.fullmatch(r"[0-9a-f]{40}", next(iter(commits))):
        raise SystemExit(
            f"{definition.name}: the responses' x-dfo-commit headers name "
            f"{sorted(commits)}, not one commit; start a local app with "
            "DFO_BUILD_COMMIT set to its checkout's commit"
        )
    origin = {
        "kind": "recorded",
        "base_url": ctx.base_url,
        "dfo_commit": commits.pop(),
        "recorded_at": recorded_at,
    }
    derived = Derived(session.call, session.recorder.exchanges, session.expect)
    record = _record(definition.name, definition.description, origin, derived)
    write_record(directory, record, session.recorder.bodies)


def record_synthetic(definition: Synthetic, ctx: Context, directory: Path) -> None:
    parent_path = directory / f"{definition.derived_from}.json"
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    derived = definition.derive(parent, ctx)
    origin = {
        "kind": "synthetic",
        "derived_from": definition.derived_from,
        "edit": definition.edit,
    }
    write_record(
        directory,
        _record(definition.name, definition.description, origin, derived),
        {},
    )


def binary_total(directory: Path) -> int:
    return sum(
        p.stat().st_size
        for p in directory.iterdir()
        if p.is_file() and p.suffix != ".json"
    )


def _rows(path: Path) -> dict[str, dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))["codes"]
    return {row["code"]: row for row in rows}


def load_actions(package_dir: Path) -> dict[str, dict[str, Any]]:
    return _rows(package_dir / "actions.json")


def load_client_codes(package_dir: Path) -> dict[str, dict[str, Any]]:
    return _rows(package_dir / "client-codes.json")


class Served:
    """A transport serving one record's exchanges in order, its binary bodies
    read from `body_dir`. A request the record does not hold next raises
    `ValueError`."""

    def __init__(self, record: dict[str, Any], body_dir: Path) -> None:
        self.name = record["name"]
        self.base_url = record["origin"].get("base_url", DEV_BASE_URL)
        self.body_dir = body_dir
        self.pending = copy.deepcopy(record["exchanges"])

    def __call__(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout: float,
    ) -> Response:
        if not self.pending:
            raise ValueError(f"{self.name}: {method} {url} follows the last exchange")
        request, response = self.pending[0]["request"], self.pending[0]["response"]
        if "url" in request:
            target, sent = request["url"], redact_external_url(url)
            if headers:
                raise ValueError(f"{self.name}: a signed download carries headers")
        elif "origin_path" in request:
            target, sent = _origin(self.base_url) + request["origin_path"], url
        else:
            target, sent = self.base_url + request["path"], url
        posted = json.loads(body) if body else None
        if (method, sent, posted) != (request["method"], target, request.get("json")):
            raise ValueError(
                f"{self.name}: {method} {sent} is not the next exchange, "
                f"{request['method']} {target}"
            )
        self.pending.pop(0)
        if "json" in response:
            data = json.dumps(response["json"]).encode()
        elif "body_file" in response:
            data = (self.body_dir / response["body_file"]).read_bytes()
        else:
            data = b""
        return Response(response["status"], dict(response.get("headers", {})), data)


def _offline(method: str, url: str, **_: Any) -> Response:
    raise SystemExit(f"--offline makes no request, and a scenario sent {method} {url}")


def drive(session: Session, call: Sequence[dict[str, Any]]) -> None:
    """Runs a record's steps through `session`."""
    answer = None
    for step in call:
        match step:
            case {"op": "search", "question": question}:
                answer = session.search(question)
            case {"op": "choose", "option": option}:
                if answer is None:
                    raise ValueError("a choice follows no answer")
                answer = session.choose(answer, option, part=step.get("part"))
            case {"op": "fetch", "spec": spec}:
                window = step.get("window")
                session.fetch_spec(
                    Spec(spec["spec_id"], spec["definition"], spec.get("sentence")),
                    window=tuple(window) if isinstance(window, list) else window,
                    facets=step.get("facets"),
                )
            case {"op": "fetch", "ref": ref}:
                session.fetch_ref(ref)
            case {"op": "fetch"}:
                if answer is None:
                    raise ValueError("a fetch of an answer follows no answer")
                session.fetch_answer(
                    answer,
                    partial=step.get("partial", False),
                    strict=step.get("strict", False),
                )
            case {"op": "mint", "spec": spec}:
                session.mint(
                    Spec(spec["spec_id"], spec["definition"], spec.get("sentence"))
                )
            case {"op": "result"}:
                session.result()


def rederive_recorded(name: str, ctx: Context, directory: Path) -> None:
    """Derives a recorded scenario's `expect` again from its own steps over
    its own exchanges, with no request, and keeps the rest of the record."""
    path = directory / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{name}: no record to derive from; record it live")
    record = json.loads(path.read_text(encoding="utf-8"))
    served = Served(record, directory)
    session = Session(
        Recorder(name, served.base_url, served),
        ctx.actions,
        ctx.client_codes,
        read_shape=ctx.read_shape,
        sleep=lambda _: None,
    )
    try:
        drive(session, record["call"])
    except (ValueError, ShapeError) as exc:
        raise SystemExit(f"{name}: {exc}; record it live") from exc
    unchanged = (
        not served.pending
        and session.call == record["call"]
        and session.recorder.exchanges == record["exchanges"]
    )
    if not unchanged:
        raise SystemExit(f"{name}: its steps no longer make its exchanges")
    write_record(
        directory, {**record, "expect": session.expect}, session.recorder.bodies
    )


def run_record(args: argparse.Namespace) -> int:
    package_dir: Path = args.package_dir
    directory: Path = args.scenarios_dir or package_dir / "scenarios"
    names = {d.name for d in (*RECORDED, *SYNTHETIC)}
    unknown = set(args.only or ()) - names
    if unknown:
        raise SystemExit(f"no scenario is named {sorted(unknown)}")
    chosen = set(args.only or names)
    live = sorted(d.name for d in SYNTHETIC if d.live and d.name in chosen)
    if args.offline and live:
        raise SystemExit(f"--offline derives no live scenario, and {live} are live")
    ctx = Context(
        args.base_url,
        _offline if args.offline else urllib_transport,
        load_actions(package_dir),
        load_client_codes(package_dir),
    )
    count = 0
    for definition in RECORDED:
        if definition.name not in chosen:
            continue
        if args.offline:
            print(f"deriving {definition.name} from its record", flush=True)
            rederive_recorded(definition.name, ctx, directory)
        else:
            print(f"recording {definition.name}", flush=True)
            record_recorded(definition, ctx, directory)
        count += 1
    for synthetic in SYNTHETIC:
        if synthetic.name in chosen:
            print(f"deriving {synthetic.name}", flush=True)
            record_synthetic(synthetic, ctx, directory)
            count += 1
    total = binary_total(directory)
    print(f"{count} scenario(s) written; binary bodies {total:,} bytes")
    if count == 0:
        return 1
    if total > BINARY_BUDGET:
        print(f"binary bodies pass the {BINARY_BUDGET:,}-byte budget", file=sys.stderr)
        return 1
    if args.scenarios_dir is not None:
        return 0
    return validate_main()


def run_openapi(args: argparse.Namespace) -> int:
    checkout = args.dfo_apis.resolve()
    document, commit = capture_openapi(checkout)
    write_served(
        args.package_dir, document, commit, "dfo_apis.app.create_app() in process"
    )
    print(f"served/openapi.json captured from {REPOSITORY} at {commit}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--package-dir", type=Path, default=PACKAGE_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    openapi = commands.add_parser("openapi", help="capture served/openapi.json")
    openapi.add_argument(
        "--dfo-apis", type=Path, required=True, help="a dfo-apis checkout"
    )
    record = commands.add_parser("record", help="record the scenarios")
    record.add_argument("--base-url", default=DEV_BASE_URL)
    record.add_argument("--only", nargs="+", metavar="NAME")
    record.add_argument(
        "--offline",
        action="store_true",
        help="derive each outcome again from the records, with no request",
    )
    record.add_argument(
        "--scenarios-dir",
        type=Path,
        help="write here, outside the package, unvalidated",
    )
    args = parser.parse_args(argv)
    return run_openapi(args) if args.command == "openapi" else run_record(args)


if __name__ == "__main__":
    sys.exit(main())
