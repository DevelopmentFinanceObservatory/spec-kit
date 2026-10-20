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
        [--only NAME ...] [--scenarios-dir DIR]

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
scenario is named for and refuses to write one that does not. Synthetic
scenarios are derived from the recorded records after them, so a recording
refreshes both. Signed storage URLs keep their path, and their query becomes
`X-Goog-Signature=redacted`. The run ends by validating the whole contract
with `tools/validate_client_contract.py`, and fails when it reports a
problem or the binary bodies pass 5 MB.

The module imports only the standard library at load time, so its tests run
without pyarrow.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
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
from urllib.parse import parse_qsl, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "python" / "client-contract" / "src" / "dfo_client_contract"
DEV_BASE_URL = "https://dfo-dev-apis-zms5lj4zhq-nn.a.run.app/api"
REPOSITORY = "DevelopmentFinanceObservatory/apis"
BINARY_BUDGET = 5_000_000

# The rendering both clients ask for: bare long Parquet.
RENDERING = {"format": "parquet", "bundle": False}
# The response headers a client reads, and so the ones a record keeps.
KEPT_HEADERS = ("content-type", "location", "retry-after")
# The variable text an identity resubmit sends for a member no answer part names.
UNNAMED_VARIABLE_TEXT = "dataset"
POLL_PATH = "/datasets/operations/{operation_id}"

# The class each action in actions.json maps to, as the contract README states.
ACTION_CLASSES = {
    "fix_request": "InvalidRequestError",
    "check_id": "NotFoundError",
    "choose": "IncompleteAnswerError",
    "ask_data_question": "IncompleteAnswerError",
    "take_newest_version": "VersionUnavailableError",
    "log_in": "AuthenticationError",
    "request_scope": "PermissionDeniedError",
    "wait": "RateLimitError",
    "wait_for_reset": "QuotaExceededError",
    "report": "ServerError",
    "upgrade": "ContractError",
}
STATUS_CLASSES = {
    400: "InvalidRequestError",
    401: "AuthenticationError",
    403: "PermissionDeniedError",
    404: "NotFoundError",
    422: "InvalidRequestError",
    429: "RateLimitError",
}


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
    """`app.openapi()` of the composed app in `checkout`, and its commit."""
    if _git(checkout, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"{checkout} has uncommitted changes to tracked files")
    commit = _git(checkout, "rev-parse", "HEAD")
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
            raise SystemExit(
                f"the capture failed in {checkout}; its environment needs "
                "`uv sync --extra eval --extra api --extra engine --extra datasets "
                f"--extra graph`:\n{result.stderr}"
            )
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


def redact_signed_url(url: str) -> str:
    """`url` with a signed query replaced by `X-Goog-Signature=redacted`."""
    parts = urlsplit(url)
    if not any(name.startswith("X-Goog-") for name, _ in parse_qsl(parts.query)):
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
        return self._send("GET", {"url": redact_signed_url(url)}, url, None, bare=True)

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
            name: redact_signed_url(response.headers[name])
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


@dataclass(frozen=True, slots=True)
class Answer:
    body: dict[str, Any]
    parts: list[dict[str, Any]]
    sentence: str | None
    status: str | None


def answer_from_body(body: dict[str, Any]) -> Answer:
    return Answer(
        body, body.get("parts", []), body.get("rendered_sentence"), body.get("status")
    )


def answer_gaps(answer: Answer, gap_kinds: Mapping[str, str]) -> list[dict[str, Any]]:
    """Each part without a reading: a candidates part is a choice among its
    specs; a no_data part takes its reason's gap kind, or `timed_out` when the
    answer timed out; a part of unknown status is an `unknown` gap."""
    timed_out = answer.body.get("diagnostics", {}).get("timed_out") is True
    gaps = []
    for index, part in enumerate(answer.parts):
        match part.get("status"):
            case "definite":
                continue
            case "candidates":
                kind, options = "choice", len(part["candidates"]["specs"])
            case "no_data":
                no_data = part["no_data"]
                kind = (
                    "timed_out"
                    if timed_out
                    else gap_kinds.get(no_data["reason"], "no_data")
                )
                options = len(no_data.get("options", []))
            case "unsupported":
                kind, options = "unsupported", 0
            case _:
                kind, options = "unknown", 0
        gaps.append({"part": index, "kind": kind, "options": options})
    return gaps


def answer_outcome(answer: Answer, gap_kinds: Mapping[str, str]) -> dict[str, Any]:
    """An answer outcome. A declined or catalogue answer is never complete."""
    gaps = answer_gaps(answer, gap_kinds)
    outcome: dict[str, Any] = {"kind": answer.body["kind"]}
    if answer.status is not None:
        outcome["status"] = answer.status
    outcome["sentence"] = answer.sentence
    outcome["complete"] = answer.body["kind"] == "resolution" and not gaps
    outcome["specs"] = list(dict.fromkeys(spec.spec_id for _, spec in _members(answer)))
    outcome["gaps"] = gaps
    return {"answer": outcome}


def _members(answer: Answer) -> list[tuple[list[str], Spec]]:
    """One member per distinct spec_id of the definite parts, in part order,
    with the variable text of every part it answers."""
    members: dict[str, tuple[list[str], Spec]] = {}
    for part in answer.parts:
        if part.get("status") != "definite":
            continue
        spec = _part_spec(part["spec"])
        members.setdefault(spec.spec_id, ([], spec))[0].append(part["variable_text"])
    return list(members.values())


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
    members: Sequence[Member], *, missing: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """A dataset outcome. Several members stack into one long table with a
    `part` column first, then every member's columns in member order."""
    if len(members) == 1:
        columns = list(members[0].columns)
    else:
        columns = ["part", *dict.fromkeys(c for m in members for c in m.columns)]
    return {
        "dataset": {
            "rows": sum(member.rows for member in members),
            "columns": columns,
            "complete": not missing,
            "members": [
                {
                    "parts": member.parts,
                    "spec_id": member.spec_id,
                    "content_version": member.content_version,
                    "sentence": member.sentence,
                }
                for member in members
            ],
            "missing": [{"part": gap["part"], "kind": gap["kind"]} for gap in missing],
        }
    }


def parquet_shape(data: bytes) -> tuple[int, list[str]]:
    import pyarrow.parquet as pq  # noqa: PLC0415 - optional, so the tests run without it

    parquet = pq.ParquetFile(io.BytesIO(data))
    return parquet.metadata.num_rows, list(parquet.schema_arrow.names)


def gap_kinds(actions: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """The gap kind of each no_data reason in actions.json."""
    return {code: row["gap_kind"] for code, row in actions.items() if "gap_kind" in row}


def _reached[T](results: list[T], step: str) -> T:
    """The result a step produced; a step that refused cannot be acted on."""
    if not results:
        raise ShapeError(f"{step} refused, so the steps after it cannot run")
    return results[0]


class StepError(Exception):
    """A step ended in an error a client raises."""

    def __init__(self, error_class: str, code: str | None) -> None:
        super().__init__(f"{error_class} {code}")
        self.outcome = {"error": {"class": error_class, "code": code}}


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
        except StepError as refusal:
            outcome = refusal.outcome
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
        self, answer: Answer, option: int | str, *, part: int | None = None
    ) -> Answer:
        """A choice in a one-part answer is local for candidates and posts the
        option's data_query for a no_data part. In a several-part answer it
        posts dataset_resubmit, with the chosen option's part as a parsed
        entry, each definite part as an identity entry and each other part's
        restatement as a parsed entry."""
        index = (
            part if part is not None else answer_gaps(answer, self.gap_kinds)[0]["part"]
        )
        chosen = answer.parts[index]
        step: dict[str, Any] = {"op": "choose"}
        if part is not None:
            step["part"] = part
        step["option"] = option
        answers: list[Answer] = []

        def run() -> dict[str, Any]:
            if chosen["status"] == "candidates":
                require(len(answer.parts) == 1, "a candidates choice here is one-part")
                if not isinstance(option, int):
                    raise ShapeError("candidates are chosen by index here")
                spec = chosen["candidates"]["specs"][option]
                part_ = {
                    "status": "definite",
                    "variable_text": chosen["variable_text"],
                    "spec": spec,
                }
                answers.append(
                    Answer(
                        answer.body,
                        [part_],
                        spec["resolved"]["rendered_sentence"],
                        "definite",
                    )
                )
                return self._answered(answers[0])
            options = chosen["no_data"]["options"]
            picked = next(
                (o for i, o in enumerate(options) if option in (i, o["label"])), None
            )
            if picked is None:
                raise ShapeError(f"the gap offers no option {option!r}")
            if len(answer.parts) == 1:
                answers.append(self._resolve(picked["data_query"]))
                return self._answered(answers[0])
            entries = [
                {"entry_kind": "parsed", "part": picked["data_query"]["parts"][0]}
                if i == index
                else _resubmit_entry(p)
                for i, p in enumerate(answer.parts)
            ]
            answers.append(
                self._resolve({"kind": "dataset_resubmit", "entries": entries})
            )
            return self._answered(answers[0])

        self._step(step, run)
        return _reached(answers, "the choice")

    def fetch_answer(self, answer: Answer, *, partial: bool = False) -> None:
        step: dict[str, Any] = {"op": "fetch"}
        if partial:
            step["partial"] = True

        def run() -> dict[str, Any]:
            if answer.body["kind"] != "resolution":
                raise StepError("DeclinedError", None)
            gaps = answer_gaps(answer, self.gap_kinds)
            if gaps and not partial:
                raise StepError("IncompleteAnswerError", None)
            members = [
                self.build(parts, spec, None) for parts, spec in _members(answer)
            ]
            return dataset_outcome(members, missing=gaps)

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
        problem = response.json()
        code = problem.get("code")
        row = self.actions.get(code)
        if row is not None:
            raise StepError(ACTION_CLASSES[row["action"]], code)
        fallback = "ServerError" if response.status >= 500 else "InvalidRequestError"
        raise StepError(STATUS_CLASSES.get(response.status, fallback), code)


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


def _resubmit_entry(part: dict[str, Any]) -> dict[str, Any]:
    if part["status"] == "definite":
        spec = part["spec"]
        entry = {
            "entry_kind": "identity",
            "variable_text": part["variable_text"],
            "identity": spec["definition"]["identity"],
        }
        groups = spec["resolved"].get("entity_groups")
        if groups:
            entry["entity_groups"] = groups
        return entry
    return {"entry_kind": "parsed", "part": part["restatement"]}


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
ASYNC = "ODA received by every recipient country from every provider (DAC2a) from 2018 to 2024"
NARROW_WINDOW = (2020, 2023)
SYNC_ROWS = 100_000


@dataclass
class Context:
    """Prerequisites a scenario needs but does not record, fetched once."""

    base_url: str
    transport: Transport
    actions: Mapping[str, Mapping[str, Any]]
    read_shape: Callable[[bytes], tuple[int, list[str]]] = parquet_shape
    _cache: dict[str, Any] = field(default_factory=dict)

    def session(self, name: str, **recorder: Any) -> Session:
        return Session(
            Recorder(name, self.base_url, self.transport, **recorder),
            self.actions,
            read_shape=self.read_shape,
        )

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
    name: str
    derived_from: str
    edit: str
    description: str
    derive: Callable[[dict[str, Any], Context], Derived]


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
    error = {"error": {"class": "ServerError", "code": "transient_unavailable"}}
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


def _unknown_part_status(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    body = exchanges[0]["response"]["json"]
    body["parts"][1]["status"] = "deferred"
    expect = copy.deepcopy(parent["expect"])
    expect[0] = answer_outcome(answer_from_body(body), gap_kinds(ctx.actions))
    return Derived(copy.deepcopy(parent["call"]), exchanges, expect)


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
    error = {"error": {"class": "ContractError", "code": None}}
    return Derived(
        copy.deepcopy(parent["call"]), exchanges, [parent["expect"][0], error]
    )


def _place_choice(
    option: str, part: int | None
) -> Callable[[dict[str, Any], Context], Derived]:
    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        canned = [
            {
                **e,
                "response": {
                    **e["response"],
                    "json": with_place_options(e["response"]["json"]),
                },
            }
            for e in copy.deepcopy(parent["exchanges"])
        ]
        session = ctx.session("synthetic", canned=canned, edit=with_place_options)
        answer = session.search(parent["call"][0]["question"])
        session.choose(answer, option, part=part)
        return Derived(session.call, session.recorder.exchanges, session.expect)

    return derive


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
        _unknown_part_status,
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
        _place_choice("Congo [DRC]", None),
    ),
    Synthetic(
        "choose-place-two-parts",
        "search-two-parts-place-ambiguous",
        "Each refusal's data_query becomes the no_data options Search serves, and the chosen option's answer is recorded live.",
        'Choosing "Congo [DRC]" on part 1 of a two-part answer posts dataset_resubmit with one '
        "entry per part in part order: the chosen option's data_query part as a parsed entry, "
        "and part 0, still a refusal, as a parsed entry of its restatement. Part 1 answers "
        "definite and part 0 refuses again with its two options.",
        _place_choice("Congo [DRC]", 1),
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
    """Writes `record` and its bodies, removing the body files an earlier
    record of the same name named."""
    path = directory / f"{record['name']}.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        for exchange in old["exchanges"]:
            body_file = exchange["response"].get("body_file")
            if body_file:
                (directory / body_file).unlink(missing_ok=True)
    for body_file, data in bodies.items():
        (directory / body_file).write_bytes(data)
    _write_json(path, record)


def _renamed_bodies(
    parent: dict[str, Any], derived: Derived, name: str, directory: Path
) -> dict[str, bytes]:
    """Copies of the parent's body files the derived exchanges keep, renamed
    to start with `name`."""
    bodies: dict[str, bytes] = {}
    renamed: dict[str, str] = {}
    for exchange in derived.exchanges:
        old = exchange["response"].get("body_file")
        if old is None:
            continue
        if old not in renamed:
            renamed[old] = name + old.removeprefix(parent["name"])
            bodies[renamed[old]] = (directory / old).read_bytes()
        exchange["response"]["body_file"] = renamed[old]
    return bodies


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
    bodies = _renamed_bodies(parent, derived, definition.name, directory)
    origin = {
        "kind": "synthetic",
        "derived_from": definition.derived_from,
        "edit": definition.edit,
    }
    write_record(
        directory,
        _record(definition.name, definition.description, origin, derived),
        bodies,
    )


def binary_total(directory: Path) -> int:
    return sum(
        p.stat().st_size
        for p in directory.iterdir()
        if p.is_file() and p.suffix != ".json"
    )


def _load_validator() -> Any:
    path = Path(__file__).resolve().parent / "validate_client_contract.py"
    spec = importlib.util.spec_from_file_location("validate_client_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_client_contract"] = module
    spec.loader.exec_module(module)
    return module


def load_actions(package_dir: Path) -> dict[str, dict[str, Any]]:
    rows = json.loads((package_dir / "actions.json").read_text(encoding="utf-8"))[
        "codes"
    ]
    return {row["code"]: row for row in rows}


def run_record(args: argparse.Namespace) -> int:
    package_dir: Path = args.package_dir
    directory: Path = args.scenarios_dir or package_dir / "scenarios"
    names = {d.name for d in (*RECORDED, *SYNTHETIC)}
    unknown = set(args.only or ()) - names
    if unknown:
        raise SystemExit(f"no scenario is named {sorted(unknown)}")
    chosen = set(args.only or names)
    ctx = Context(args.base_url, urllib_transport, load_actions(package_dir))
    count = 0
    for definition in RECORDED:
        if definition.name in chosen:
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
    return _load_validator().main()


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
        "--scenarios-dir",
        type=Path,
        help="write here, outside the package, unvalidated",
    )
    args = parser.parse_args(argv)
    return run_openapi(args) if args.command == "openapi" else run_record(args)


if __name__ == "__main__":
    sys.exit(main())
