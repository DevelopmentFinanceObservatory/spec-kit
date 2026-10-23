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

`openapi` builds the composed dfo-apis app from the checkout at PATH, with
the deployed surfaces (Search, Datasets, Graph and Presentation), by
calling `dfo_apis.app.create_app()` in that checkout's environment. It
writes `app.openapi()` to `served/openapi.json` and the checkout's commit to
`served/SOURCE.json`. No server runs. The surfaces load an origin catalogue
and a graph release when they are built, so the capture publishes the
checkout's own test catalogue and test release into a temporary directory.
The document does not depend on the data they hold. A checkout with
uncommitted changes to tracked files is refused, because the commit would
not describe the document.

`record` runs every scenario this file defines against the API at the base
URL (dev by default, or a local app at `http://127.0.0.1:8083/api`), the way
a client would, and writes each record and its binary bodies to `scenarios/`.
Each recorded scenario checks that its live answer has the shape the
scenario is named for, and the run refuses to write one that does not. With
`--offline` no request is made. Each recorded scenario's steps run again
over its own exchanges and rewrite its `expect`, and each synthetic scenario
is derived again from its parent. A synthetic derivation makes no request
in either mode. Synthetic scenarios are derived from the recorded records
after them, so a recording refreshes both, and a synthetic record names its
parent's body files and holds no copies. Every absolute URL a record keeps,
a signed download or a `location` header, keeps its path and has its query
replaced by `X-Goog-Signature=redacted`. A local app's blob URL becomes the
storage URL a deployment signs. A record holding any other absolute URL,
outside a `location` under the base URL, is refused. The long builds need
two specs the server has not built, so a local recording starts on a fresh
datasets database. `--scenarios-dir` writes outside the package,
unvalidated. The run ends by validating the whole contract with
`tools/validate_client_contract.py`, and fails when it reports a problem or
the binary bodies pass 5 MB.

The outcome rules, error classes, gap kinds and long-build pacing come
from `tools/validate_client_contract.py`, so a record's `expect` is written
by the code that checks it. pyarrow loads only when a Parquet body is read, so
the tests run without it.
"""

from __future__ import annotations

import argparse
import copy
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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from validate_client_contract import (
    FILE_EXPIRED,
    FILE_GONE,
    FILES_ROUTE_FILE,
    FILES_ROUTE_REDIRECT,
    NO_VERSIONS,
    POLL_BACKOFF_CAP,
    POLL_BACKOFF_FIRST,
    POLL_PATH,
    REBUILD_REFUSED,
    SIGNED_FETCH_STATUS,
    STORAGE_ORIGIN,
    TRACE_HEADER,
    VERSION_UNAVAILABLE,
    Answer,
    ClientError,
    LongBuild,
    absolute_url_problems,
    answer_from_body,
    answer_gaps,
    answer_members,
    answer_outcome,
    artifact_route,
    body_path,
    build_body,
    candidate_label,
    choice_for,
    choice_input,
    data_warning,
    dataset_summary,
    digest_of,
    download_target,
    failed_operation,
    gap_kinds,
    header_warnings,
    held_spec,
    may_retry,
    newest_listed,
    owns_body_file,
    part_label,
    pick_artifact,
    posted_scope,
    problem_body,
    read_from,
    refusal_error,
    rendering_for,
    resubmitted_sentence,
    retry_wait,
    search_body,
    search_warning_code,
    search_warns,
    selector_path,
    sentence_entry,
    shared_cacheable,
    surface_problems,
    trace_id,
    untraced,
    versions_path,
    with_step_notes,
)
from validate_client_contract import main as validate_main

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "python" / "client-contract" / "src" / "dfo_client_contract"
DEV_BASE_URL = "https://dfo-dev-apis-zms5lj4zhq-nn.a.run.app/api"
REPOSITORY = "DevelopmentFinanceObservatory/apis"
BINARY_BUDGET = 5_000_000

# The rendering both clients ask for, bare long Parquet.
RENDERING = {"format": "parquet", "bundle": False}
# The response headers a client reads, and so the ones a record keeps, beside
# a `cache-control` that marks the response shared-cacheable, which excuses it
# from carrying `x-dfo-trace-id`.
KEPT_HEADERS = (
    "content-type",
    "location",
    "retry-after",
    "deprecation",
    "sunset",
    "x-dfo-warning",
    "x-dfo-trace-id",
)


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
    from tests.graph_release.support import build_session_release
    from tests.origin_catalogue.support import publish_sample

    os.environ["ORIGIN_CATALOGUE_URI"] = f"file://{tmp}/origin-catalogue"
    publish_sample(os.environ["ORIGIN_CATALOGUE_URI"])
    os.environ["GRAPH_RELEASE_URI"] = f"file://{tmp}/releases"
    build_session_release(os.environ["GRAPH_RELEASE_URI"])
    from dfo_apis.app import create_app

    document = create_app().openapi()
Path(sys.argv[1]).write_text(json.dumps(document), encoding="utf-8")
"""

# The deployed composition's surface flags, Search always mounted beside them,
# and placeholders the surfaces need to be built, none of which reaches the
# document. Skills is not deployed, so its routes stay out of the document.
_CAPTURE_ENV = {
    "SEARCH_GRAPH_BASE": "https://graph.offline.invalid",
    "DFO_PUBLIC_SITE_URL": "https://site.example",
    "DFO_DATASETS_SURFACE": "1",
    "DFO_GRAPH_SURFACE": "1",
    "DFO_PRESENTATION_SURFACE": "1",
}
_PASSED_ENV = ("HOME", "PATH", "TMPDIR", "UV_CACHE_DIR", "UV_PYTHON")
# The dfo-apis extras the composed app imports.
_EXTRAS = tuple(
    f"--extra={extra}" for extra in ("eval", "api", "engine", "datasets", "graph")
)


def capture_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The capture's environment: a few of the caller's variables and the
    placeholders, so a variable in the caller's shell cannot reach the app."""
    env = {name: environ[name] for name in _PASSED_ENV if name in environ}
    env.update(_CAPTURE_ENV)
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
            env=capture_env(os.environ),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(f"the capture failed in {checkout}:\n{result.stderr}")
        document = json.loads(out.read_text(encoding="utf-8"))
    if problems := surface_problems(document):
        raise SystemExit(
            "the captured document is not the deployed composition:\n"
            + "\n".join(problems)
        )
    return document, commit


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
    """Leaves a redirect to the caller, since the files route's 302 is read by hand."""

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


# A local composed app signs no URL. Its launcher serves each file at
# `/__e2e__/blob/{bucket}/{object}` on the API's own origin. A record states
# such a URL as the storage URL a deployment signs for the same object.
LOCAL_BLOB_PREFIX = "/__e2e__/blob/"


def redact_external_url(url: str) -> str:
    """An absolute `url` with its query, whatever signs it, replaced by
    `X-Goog-Signature=redacted`, and a local app's blob URL rewritten to
    `STORAGE_ORIGIN/{bucket}/{object}`, the object name percent-encoded as
    storage signs it. A root-relative URL is returned as it stands."""
    parts = urlsplit(url)
    if not parts.netloc:
        return url
    if parts.path.startswith(LOCAL_BLOB_PREFIX):
        bucket, _, name = parts.path.removeprefix(LOCAL_BLOB_PREFIX).partition("/")
        path = f"/{bucket}/{quote(unquote(name), safe='/')}"
        parts = urlsplit(STORAGE_ORIGIN)._replace(path=path)
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

    `canned` exchanges answer the first requests, in order, before the
    network. A request that is not the next canned one ends the run."""

    name: str
    base_url: str
    transport: Transport
    canned: list[dict[str, Any]] = field(default_factory=list)
    exchanges: list[dict[str, Any]] = field(default_factory=list)
    bodies: dict[str, bytes] = field(default_factory=dict)
    commits: set[str] = field(default_factory=set)
    canned_served: int = 0
    warned: set[tuple[str, ...]] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    read_trace: str | None = None

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
        if self.canned:
            if self.canned[0]["request"] != request:
                raise SystemExit(
                    f"{self.name}: {method} {url} is not the canned request "
                    f"{json.dumps(self.canned[0]['request'])[:300]}"
                )
            exchange = self.canned.pop(0)
            self.exchanges.append(exchange)
            self.canned_served += 1
            self._traced(exchange["response"], bare=bare)
            return self._warned(_canned_response(exchange["response"]), bare=bare)

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
        if not bare and untraced(response.headers):
            raise SystemExit(
                f"{self.name}: {method} {url} answered {response.status} with no "
                f"{TRACE_HEADER} header, which the server sends on every response "
                "but a shared-cacheable one"
            )
        kept = self._keep(response)
        self.exchanges.append({"request": request, "response": kept})
        self._traced(kept, bare=bare)
        return self._warned(response, bare=bare)

    def _traced(self, kept: Mapping[str, Any], *, bare: bool) -> None:
        """Notes an API response's trace id, as the record keeps it, as the
        one an error raised on reading it carries."""
        if not bare:
            self.read_trace = trace_id(kept.get("json"), kept.get("headers", {}))

    def _warned(self, response: Response, *, bare: bool) -> Response:
        """Notes the warnings an API response's headers issue."""
        if not bare:
            self.warnings.extend(header_warnings(response.headers, self.warned))
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
        if shared_cacheable(response.headers):
            headers["cache-control"] = response.headers["cache-control"]
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
    entity_groups: dict[str, Any] | None = None

    def step(self) -> dict[str, Any]:
        step: dict[str, Any] = {"spec_id": self.spec_id, "definition": self.definition}
        if self.sentence is not None:
            step["sentence"] = self.sentence
        if self.entity_groups:
            step["entity_groups"] = self.entity_groups
        return step


def _step_spec(step: Mapping[str, Any]) -> Spec:
    """The Spec a step's `spec`, or `held_spec`'s shape, holds."""
    return Spec(
        step["spec_id"],
        step["definition"],
        step.get("sentence"),
        step.get("entity_groups"),
    )


def _part_spec(spec: dict[str, Any]) -> Spec:
    return _step_spec(held_spec(spec))


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
    rendering: dict[str, Any] = field(default_factory=lambda: dict(RENDERING))

    @property
    def spec_id(self) -> str:
        return self.definition["spec_id"]

    def ref(self) -> dict[str, Any]:
        return {
            "parts": self.parts,
            "sentence": self.sentence,
            "spec": {"spec_id": self.spec_id, "definition": self.definition},
            "content_version": self.content_version,
            "artifact": {"digest": self.digest, "rendering": dict(self.rendering)},
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
    """The result a step produced. A step that refused cannot be acted on."""
    if not results:
        raise ShapeError(f"{step} refused, so the steps after it cannot run")
    return results[0]


class ShapeError(Exception):
    """The live answer lacks the shape the scenario is named for."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ShapeError(message)


@dataclass(frozen=True, slots=True)
class Minted:
    """The operation a mint started or a resume read: its resource, the
    headers that came with it, the build request a failed operation posts
    again (None for a resumed one), the spec the client holds, and the
    rendering it downloads."""

    resource: dict[str, Any]
    headers: dict[str, str]
    request: dict[str, Any] | None
    spec: Spec | None
    rendering: dict[str, Any]


def _step_options(
    step: dict[str, Any], *, layout: str | None, strict: bool
) -> dict[str, Any]:
    if layout is not None:
        step["layout"] = layout
    if strict:
        step["strict"] = True
    return step


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
    minted: Minted | None = None
    long_builds: list[dict[str, Any]] = field(default_factory=list)
    retry_waits: list[float] = field(default_factory=list)

    @property
    def gap_kinds(self) -> dict[str, str]:
        return gap_kinds(self.actions)

    def _step(self, step: dict[str, Any], run: Callable[[], dict[str, Any]]) -> None:
        self.call.append(step)
        self.long_builds, self.retry_waits = [], []
        self.recorder.warnings = []
        self.recorder.read_trace = None
        try:
            outcome = run()
        except ClientError as error:
            trace = self.recorder.read_trace
            outcome = read_from(error, trace).outcome(self.client_codes)
        self.expect.append(
            with_step_notes(
                outcome,
                long_builds=self.long_builds,
                retry_waits=self.retry_waits,
                warnings=self.recorder.warnings,
            )
        )

    def _answered(self, answer: Answer) -> dict[str, Any]:
        return answer_outcome(answer, self.gap_kinds)

    def _retried(
        self, request: dict[str, Any], send: Callable[[], Response]
    ) -> Response:
        """Sends `request` through `send`, and again after each refusal
        `may_retry` allows, waiting `retry_wait` before each retry."""
        response = send()
        answers = [_read(response)]
        while may_retry(request, answers):
            wait = retry_wait(response.headers, len(answers) - 1)
            self.retry_waits.append(wait)
            self.sleep(wait)
            response = send()
            answers.append(_read(response))
        return response

    def _api(self, method: str, path: str, json_body: Any = None) -> Response:
        """A request under the base URL, retried as a client retries it."""
        request: dict[str, Any] = {"method": method, "path": path}
        if json_body is not None:
            request["json"] = json_body
        return self._retried(
            request, lambda: self.recorder.api(method, path, json_body)
        )

    def _resolve(self, input_: dict[str, Any]) -> Answer:
        response = self._api("POST", "/search/resolve", search_body(input_))
        answer = answer_from_body(self._acted(response))
        return replace(answer, trace_id=self.recorder.read_trace)

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
        posting the choice to Search, and returns the answer Search gives, or
        None when the choice refused."""
        step: dict[str, Any] = {"op": "choose"}
        if part is not None:
            step["part"] = part
        step["option"] = option
        answers: list[Answer] = []

        def run() -> dict[str, Any]:
            try:
                choice = choice_for(answer, self.gap_kinds, part, option)
                posts = choice_input(answer, choice)
            except ClientError as error:
                raise read_from(error, answer.trace_id) from None
            answers.append(self._resolve(posts))
            return self._answered(answers[0])

        self._step(step, run)
        return answers[0] if answers else None

    def fetch_answer(
        self,
        answer: Answer,
        *,
        partial: bool = False,
        strict: bool = False,
        layout: str | None = None,
    ) -> None:
        """Builds the answer's readings. A declined or catalogue answer, a gap
        outside a partial fetch, a partial fetch with no reading, a strict
        fetch whose readings depart from the question, and the frame layout
        with several readings each raise before any request."""
        step: dict[str, Any] = {"op": "fetch"}
        if partial:
            step["partial"] = True

        def run() -> dict[str, Any]:
            if answer.body["kind"] != "resolution":
                raise read_from(ClientError("answer_declined"), answer.trace_id)
            gaps = answer_gaps(answer, self.gap_kinds)
            readings = _members(answer)
            if gaps and (not partial or not readings):
                raise read_from(ClientError("answer_incomplete"), answer.trace_id)
            warns = search_warns(answer)
            if warns and strict:
                raise read_from(
                    ClientError(search_warning_code(answer), error="SearchWarning"),
                    answer.trace_id,
                )
            rendering = rendering_for(layout)
            if "layout" in rendering and len(readings) > 1:
                raise ClientError("invalid_argument")
            members = [
                self.build(parts, spec, None, rendering, strict=strict)
                for parts, spec in readings
            ]
            return dataset_outcome(members, missing=gaps, search_warning=warns)

        self._step(_step_options(step, layout=layout, strict=strict), run)

    def fetch_spec(
        self,
        spec: Spec,
        *,
        window: str | tuple[int, int] | None = None,
        entities: Mapping[str, Sequence[str] | None] | None = None,
        facets: Mapping[str, str] | None = None,
        layout: str | None = None,
        strict: bool = False,
    ) -> list[Member]:
        """Builds `spec` narrowed by `window`, `entities` and `facets`. A
        role in `entities` replaces the spec's binding of it, or drops it
        when null."""
        step: dict[str, Any] = {"op": "fetch", "spec": spec.step()}
        if window is not None:
            step["window"] = list(window) if isinstance(window, tuple) else window
        if entities is not None:
            step["entities"] = {
                role: None if refs is None else list(refs)
                for role, refs in entities.items()
            }
        if facets is not None:
            step["facets"] = dict(facets)
        scope = posted_scope(step)
        built: list[Member] = []

        def run() -> dict[str, Any]:
            rendering = rendering_for(layout)
            built.append(self.build([], spec, scope, rendering, strict=strict))
            return dataset_outcome(built)

        self._step(_step_options(step, layout=layout, strict=strict), run)
        return built

    def fetch_ref(self, ref: dict[str, Any]) -> None:
        """The exact versions a ref names, through the lookup order."""

        def run() -> dict[str, Any]:
            return dataset_outcome([self._stored(member) for member in ref["members"]])

        self._step({"op": "fetch", "ref": ref}, run)

    def newest_version(self, spec: Spec) -> str | None:
        """The newest stored version of `spec`, from the versions route."""
        found: list[str | None] = []

        def run() -> dict[str, Any]:
            found.append(self._newest(spec.spec_id))
            return {"newest_version": {"content_version": found[0]}}

        self._step({"op": "newest_version", "spec": spec.step()}, run)
        return found[0] if found else None

    def mint(self, spec: Spec, *, layout: str | None = None) -> str:
        operation: list[str] = []

        def run() -> dict[str, Any]:
            rendering = rendering_for(layout)
            body = _build_body(spec, None, rendering)
            response = self._api("POST", "/datasets", body)
            resource = self._acted(response)
            require(
                response.status == 202, f"the build answered {response.status}, not 202"
            )
            self.minted = Minted(resource, response.headers, body, spec, rendering)
            operation.append(resource["operation"]["operation_id"])
            return _operation_outcome(resource)

        self._step(
            _step_options(
                {"op": "mint", "spec": spec.step()}, layout=layout, strict=False
            ),
            run,
        )
        return _reached(operation, "the mint")

    def resume(self, operation_id: str, *, layout: str | None = None) -> None:
        """Reads a build another process started, by its operation id."""

        def run() -> dict[str, Any]:
            path = POLL_PATH.format(operation_id=operation_id)
            response = self._api("GET", path)
            resource = self._acted(response)
            self.minted = Minted(
                resource, response.headers, None, None, rendering_for(layout)
            )
            return _operation_outcome(resource)

        step = {"op": "operation", "operation_id": operation_id}
        self._step(_step_options(step, layout=layout, strict=False), run)

    def result(self) -> None:
        """Waits for the operation the last mint or operation step holds."""
        if self.minted is None:
            raise ValueError("result follows mint or operation")
        minted = self.minted

        def run() -> dict[str, Any]:
            manifest = self._wait(minted.resource, minted.headers, minted.request)
            member = self._member([], minted.spec, manifest, minted.rendering, False)
            return dataset_outcome([member])

        self._step({"op": "result"}, run)

    # -- building --

    def build(
        self,
        parts: list[str],
        spec: Spec,
        scope: dict[str, Any] | None,
        rendering: dict[str, Any],
        *,
        strict: bool = False,
    ) -> Member:
        """Builds one member: the build, the sentence when the client lacks it,
        then the file."""
        manifest = self._built(_build_body(spec, scope, rendering))
        return self._member(parts, spec, manifest, rendering, strict)

    def _built(self, body: dict[str, Any]) -> dict[str, Any]:
        """The manifest a build answers, through a long build on a 202."""
        response = self._api("POST", "/datasets", body)
        reply = self._acted(response)
        if response.status != 202:
            return reply
        return self._wait(reply, response.headers, body)

    def _member(
        self,
        parts: list[str],
        spec: Spec | None,
        manifest: dict[str, Any],
        rendering: dict[str, Any],
        strict: bool,
    ) -> Member:
        if strict and (warning := data_warning(manifest)) is not None:
            raise warning
        artifact = pick_artifact(manifest, rendering)
        if artifact is None:
            raise ClientError("contract_violation")
        route = artifact_route(self.recorder.base_url, artifact["url"])
        definition = manifest["definition"]
        sentence = None if spec is None else spec.sentence
        if spec is None or definition["spec_id"] != spec.spec_id or sentence is None:
            held = None if spec is None else spec.step()
            sentence = self._identity_sentence(parts, definition, held)
        digest = artifact["artifact_digest"]
        rows, columns = self.read_shape(self._download(route, digest))
        return Member(
            parts,
            definition,
            manifest["content_version"],
            sentence,
            digest,
            rows,
            columns,
            dict(rendering),
        )

    def _identity_sentence(
        self,
        parts: list[str],
        definition: dict[str, Any],
        held: dict[str, Any] | None,
    ) -> str:
        entry = sentence_entry(parts, definition, held)
        answer = self._resolve({"kind": "dataset_resubmit", "entries": [entry]})
        return resubmitted_sentence(answer, definition["spec_id"])

    def _download(self, target: str, digest: str, *, origin: bool = True) -> bytes:
        """A file through its files route, named by an `origin_path`, as
        `artifact_route` places an artifact's url, or by a path under the base
        URL. A 302's location is fetched once, where `download_target` places
        it, a local app's blob URL on the API's origin standing for the
        storage URL a deployment signs. A 200 holds the file. Any other
        success raises ContractError `unexpected_response`. A fetch that
        answers with a status other than 200, or returns bytes of another
        digest, asks the route once more."""
        base_url = self.recorder.base_url
        if origin:
            route_url = _origin(base_url) + target
            request = {"method": "GET", "origin_path": target}
        else:
            route_url = base_url.rstrip("/") + target
            request = {"method": "GET", "path": target}
        failure = ClientError("download_failed")
        for _ in range(2):
            files = self._retried(
                request,
                lambda: (
                    self.recorder.origin("GET", target)
                    if origin
                    else self.recorder.api("GET", target)
                ),
            )
            self._raise_problem(files)
            if files.status == FILES_ROUTE_FILE:
                data = files
            elif files.status == FILES_ROUTE_REDIRECT:
                location = files.headers["location"]
                kind, at = download_target(base_url, route_url, location)
                if kind == "origin_path" and at.startswith(LOCAL_BLOB_PREFIX):
                    kind = "url"
                data = (
                    self.recorder.external(urljoin(route_url, location))
                    if kind == "url"
                    else self.recorder.origin("GET", at)
                )
                if data.status != SIGNED_FETCH_STATUS:
                    failure = ClientError("download_failed")
                    continue
            else:
                raise ClientError("unexpected_response")
            if digest_of(data.body) == digest:
                return data.body
            failure = ClientError("digest_mismatch")
        raise failure

    def _wait(
        self,
        resource: dict[str, Any],
        headers: Mapping[str, str],
        request: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Polls as `LongBuild` rules, sleeping each wait, for at most 30
        minutes."""
        build = LongBuild(resource, headers, request, self.actions)
        self.long_builds.append(build.entry)
        deadline = time.monotonic() + 1800
        while build.result is None:
            if time.monotonic() >= deadline:
                raise SystemExit(f"{build.entry['operation_id']} ran past 30 minutes")
            self.sleep(build.next_wait())
            path = POLL_PATH.format(operation_id=build.entry["operation_id"])
            response = self.recorder.api("GET", path)
            if build.polled(response.status, response.headers, _answered(response)):
                again = self._api("POST", "/datasets", request)
                build.reposted(again.status, again.headers, _answered(again))
        return build.result

    # -- stored versions --

    def _stored(self, member: dict[str, Any]) -> Member:
        """One version a ref names, through the lookup order
        `validate_client_contract._Replay.stored_version` states."""
        spec = member["spec"]
        spec_id, version = spec["spec_id"], member["content_version"]
        rendering = member["artifact"]["rendering"]
        held = Spec(spec_id, spec["definition"], member["sentence"])
        refusal: ClientError | None = None
        manifest = None
        try:
            manifest = self._acted(self._api("GET", f"/datasets/{spec_id}/{version}"))
        except ClientError as error:
            if error.code != FILE_EXPIRED:
                raise
            refusal = error
        if manifest is not None:
            built = (manifest["definition"]["spec_id"], manifest["content_version"])
            if built != (spec_id, version):
                raise ClientError("contract_violation")
            data, refusal = self._stored_file(member, manifest)
            if data is not None:
                rows, columns = self.read_shape(data)
                return Member(
                    member["parts"],
                    spec["definition"],
                    version,
                    member["sentence"],
                    member["artifact"]["digest"],
                    rows,
                    columns,
                    dict(rendering),
                )
        try:
            manifest = self._built(_build_body(held, None, rendering))
        except ClientError as error:
            if error.error_class(self.client_codes) not in REBUILD_REFUSED:
                raise
            refusal = refusal if refusal is not None else error
            newest = self._newest(spec_id)
        else:
            if manifest["definition"]["spec_id"] != spec_id:
                raise ClientError("contract_violation")
            if manifest["content_version"] == version:
                return self._member(member["parts"], held, manifest, rendering, False)
            newest = manifest["content_version"]
        raise ClientError(
            refusal.code if refusal is not None else "version_unavailable",
            error=VERSION_UNAVAILABLE,
            newest_version=newest,
            trace_id=refusal.trace_id if refusal is not None else None,
        )

    def _stored_file(
        self, member: dict[str, Any], manifest: dict[str, Any]
    ) -> tuple[bytes | None, ClientError | None]:
        digest = member["artifact"]["digest"]
        refusal = None
        artifact = next(
            (a for a in manifest["artifacts"] if a["artifact_digest"] == digest), None
        )
        if artifact is not None:
            try:
                route = artifact_route(self.recorder.base_url, artifact["url"])
                return self._download(route, digest), None
            except ClientError as error:
                if error.code not in FILE_GONE:
                    raise
                refusal = error
        path = selector_path(
            member["spec"]["spec_id"],
            member["content_version"],
            member["artifact"]["rendering"],
        )
        try:
            return self._download(path, digest, origin=False), None
        except ClientError as error:
            if error.code == "digest_mismatch":
                return None, refusal
            if error.code in FILE_GONE:
                return None, refusal if refusal is not None else error
            raise

    def _newest(self, spec_id: str) -> str | None:
        try:
            body = self._acted(self._api("GET", versions_path(spec_id)))
        except ClientError as error:
            if error.error_class(self.client_codes) in NO_VERSIONS:
                return None
            raise
        return newest_listed(body)

    def _acted(self, response: Response) -> Any:
        """The JSON body of a success. A refusal raises its error, and a
        success without a JSON body raises ContractError
        `unexpected_response`."""
        self._raise_problem(response)
        return _answered(response)

    def _raise_problem(self, response: Response) -> None:
        if response.status < 400:
            return
        raise refusal_error(
            response.status,
            problem_body(_read(response)),
            self.actions,
            response.headers,
        )


def _read(response: Response) -> dict[str, Any]:
    """A response as a record holds it: its status, headers, and its JSON
    body when it has one."""
    read: dict[str, Any] = {"status": response.status, "headers": response.headers}
    if response.body and _is_json(response.headers.get("content-type", "")):
        read["json"] = response.json()
    return read


def _answered(response: Response) -> Any:
    """A success's JSON body, which a success without one lacks, raising
    ContractError `unexpected_response`, or a refusal's problem body."""
    read = _read(response)
    if response.status >= 400:
        return problem_body(read)
    if "json" not in read:
        raise ClientError("unexpected_response")
    return read["json"]


def _operation_outcome(resource: dict[str, Any]) -> dict[str, Any]:
    operation = resource["operation"]
    return {"operation": {k: operation[k] for k in ("operation_id", "status")}}


def _build_body(
    spec: Spec, scope: dict[str, Any] | None, rendering: Mapping[str, Any]
) -> dict[str, Any]:
    held = {"spec_id": spec.spec_id, "definition": spec.definition}
    return build_body(held, scope, rendering)


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
PLACE_LABELS = ["Congo [DRC]", "Congo [Republic]"]
CANDIDATES_BESIDE_GROUP = (
    "GNI of Germany from 2015 to 2022, and ODA disbursements from Germany to "
    "sub-Saharan Africa"
)
TWO_CANDIDATES = "GNI of Germany and tax revenue of Kenya from 2015 to 2022"
CANDIDATES_BESIDE_UNRESTATED = "GNI of Germany and debt of Kenya from 2015 to 2022"
GROUP_DEFINITE = "GDP of the least developed countries"
# A definite part whose places are a named group, beside a part whose
# direction the question leaves open, which offers one option per direction.
GROUP_BESIDE_OPTIONS = "GDP of sub-Saharan Africa, and debt of Kenya"
GROUPED_FLOW = "Germany's ODA to the least developed countries"
CANDIDATES_BESIDE_ASSUMED = "GNI of Germany and aid flows to Kenya"
# Two specs just above the 100,000-row sync budget, one for the mint and one
# for the operation another process started: a version the server has built
# answers inline, so each long build needs a spec the server has not built.
ASYNC = "ODA received by every recipient country from every provider (DAC2a) from 2018 to 2024"
ASYNC_RESUMED = "ODA received by every recipient country from every provider (DAC2a) from 2017 to 2024"
NARROW_WINDOW = (2020, 2023)
REBOUND_ORIGIN = "country/FRA"
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

    def raised(
        self, code: str, response: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """The outcome of an error the client raises with `code`, on reading
        the API response `response` when one is given, or else before any
        response."""
        error = ClientError(code)
        if response is not None:
            body, headers = response.get("json"), response.get("headers", {})
            read_from(error, trace_id(body, headers))
        return error.outcome(self.client_codes)

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
            member = session.build(parts, spec, None, dict(RENDERING))
            self._cache["ref"] = {"format_version": 1, "members": [member.ref()]}
        return self._cache["ref"]

    def async_spec(self, question: str = ASYNC) -> Spec:
        """The spec `question` answers, whose estimate is just above the sync
        budget."""
        (part,) = self.definite_answer(question).parts
        rows = part["spec"]["resolved"].get("download", {}).get("rows") or 0
        require(
            SYNC_ROWS < rows < 2 * SYNC_ROWS,
            f"{rows} rows is just above the sync budget",
        )
        return _part_spec(part["spec"])

    def running_operation(self) -> str:
        """The id of a long build another process started."""
        if "operation" not in self._cache:
            self._cache["operation"] = self.session("prerequisite").mint(
                self.async_spec(ASYNC_RESUMED)
            )
        return self._cache["operation"]


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
    picked = answer.parts[0]["candidates"]["specs"][0]["spec_id"]
    chosen = s.choose(answer, 0)
    require(
        chosen is not None
        and _statuses(chosen) == ["definite"]
        and chosen.parts[0]["spec"]["spec_id"] == picked,
        "Search answers the picked reading definite",
    )
    if chosen is not None:
        s.fetch_answer(chosen)


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


def _fetch_spec_entities(
    rebinds: Mapping[str, list[str] | None],
) -> Callable[[Session, Context], None]:
    """Fetches the definite spec with `rebinds` as `entities`, and checks the
    build binds the entities the client posts."""

    def run(s: Session, ctx: Context) -> None:
        spec = ctx.definite_spec(DEFINITE)
        (member,) = s.fetch_spec(spec, entities=rebinds)
        posted = s.recorder.exchanges[0]["request"]["json"]["input"]["scope"]
        bound = member.definition["identity"]["entities"]
        require(member.spec_id != spec.spec_id, "the entities give a new spec_id")
        require(bound == posted["entities"], "the build binds the posted entities")

    return run


def _fetch_spec_grouped(
    *,
    window: tuple[int, int] | None = None,
    entities: Mapping[str, list[str] | None] | None = None,
) -> Callable[[Session, Context], None]:
    """Fetches the GROUPED_FLOW spec, whose destination is a named group, with
    the narrowing given, and checks the built sentence names the group."""

    def run(s: Session, ctx: Context) -> None:
        spec = ctx.definite_spec(GROUPED_FLOW)
        groups = spec.entity_groups or {}
        require("destination" in groups, "the destination is a named group")
        (member,) = s.fetch_spec(spec, window=window, entities=entities)
        require(member.spec_id != spec.spec_id, "the narrowing gives a new spec_id")
        require(
            groups["destination"]["label"] in member.sentence,
            "the built sentence names the destination's group",
        )

    return run


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
    s.mint(ctx.async_spec(ASYNC))
    s.result()


def _operation_resume(s: Session, ctx: Context) -> None:
    s.resume(ctx.running_operation())
    s.result()


def _fetch_spec_frame(s: Session, ctx: Context) -> None:
    (member,) = s.fetch_spec(ctx.definite_spec(DEFINITE), layout="frame")
    require(member.rendering.get("layout") == "frame", "the frame artifact is fetched")


def _answer_kind(question: str, kind: str) -> Callable[[Session, Context], None]:
    def run(s: Session, ctx: Context) -> None:
        answer = s.search(question)
        require(answer.body["kind"] == kind, f"a {kind} answer")
        s.fetch_answer(answer)

    return run


def _place_answer(s: Session, question: str, parts: int) -> Answer:
    """The answer to `question`: `parts` entity_ambiguous parts, each with an
    option per Congo."""
    answer = s.search(question)
    require(
        [p.get("no_data", {}).get("reason") for p in answer.parts]
        == ["entity_ambiguous"] * parts,
        f"{parts} entity_ambiguous part(s)",
    )
    require(
        all(
            [o["label"] for o in p["no_data"].get("options", [])] == PLACE_LABELS
            for p in answer.parts
        ),
        f"each part offers {PLACE_LABELS}",
    )
    return answer


def _search_place(question: str, parts: int) -> Callable[[Session, Context], None]:
    def run(s: Session, ctx: Context) -> None:
        _place_answer(s, question, parts)

    return run


def _choose_place(
    question: str, parts: int, part: int | str | None
) -> Callable[[Session, Context], None]:
    """Chooses "Congo [DRC]" on `part` of the answer to `question`, and checks
    the chosen part answers definite."""

    def run(s: Session, ctx: Context) -> None:
        answer = _place_answer(s, question, parts)
        chosen = s.choose(answer, PLACE_LABELS[0], part=part)
        index = part if isinstance(part, int) else 0
        require(
            chosen is not None and _statuses(chosen)[index] == "definite",
            "the chosen part answers definite",
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


def _choose_candidate_echoes_assumptions(s: Session, ctx: Context) -> None:
    answer = s.search(CANDIDATES_BESIDE_ASSUMED)
    require(_statuses(answer) == ["candidates", "definite"], "candidates, definite")
    stated = answer.parts[1].get("assumptions")
    require(bool(stated), "the definite part states assumptions")
    require(
        "reading_assumptions" in answer.parts[0],
        "the candidates part states its readings' assumptions",
    )
    chosen = s.choose(answer, 0, part=0)
    require(
        chosen is not None and _statuses(chosen) == ["definite", "definite"],
        "the choice answers both parts definite",
    )
    if chosen is not None:
        require(
            chosen.parts[1].get("assumptions") == stated,
            "the rebuilt definite part states the echoed assumptions",
        )


def _choose_option_beside_definite(s: Session, ctx: Context) -> None:
    answer = s.search(GROUP_BESIDE_OPTIONS)
    require(_statuses(answer) == ["definite", "no_data"], "definite, no_data")
    require(
        bool(answer.parts[0]["spec"]["resolved"].get("entity_groups")),
        "the definite part names an entity group",
    )
    require(
        bool(answer.parts[1]["no_data"].get("options")), "the no_data part has options"
    )
    chosen = s.choose(answer, 0, part=1)
    require(
        chosen is not None
        and _statuses(chosen)[0] == "definite"
        and _statuses(chosen)[1] in ("definite", "candidates"),
        "part 0 stays definite and part 1 answers in the chosen direction",
    )


def _fetch_ref_newest_version(s: Session, ctx: Context) -> None:
    (member,) = ctx.definite_ref()["members"]
    spec = ctx.definite_spec(DEFINITE)
    newest = s.newest_version(spec)
    require(newest == member["content_version"], "the stored version is the newest")


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
        "options. Choosing option 0 posts that candidate's identity to Search as the one "
        "dataset_resubmit identity entry, echoing the part's reading_assumptions under its "
        "spec_id as assumptions. Search answers that reading definite with its own sentence, "
        "and fetching the answer builds that candidate's spec.",
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
        "fetch-spec-entity-rebound",
        "Fetching a two-role spec with entities naming only origin posts input.scope.entities "
        "with origin replaced and destination kept from the spec's definition, because the "
        "server's scope.entities replaces the spec's whole entity binding. The build answers a "
        "new spec_id bound to both roles.",
        _fetch_spec_entities({"origin": [REBOUND_ORIGIN]}),
    ),
    Recorded(
        "fetch-spec-entity-dropped",
        "Fetching a two-role spec with entities binding origin to null posts "
        "input.scope.entities with origin dropped and destination kept, and the build answers "
        "a new spec_id bound to destination alone.",
        _fetch_spec_entities({"origin": None}),
    ),
    Recorded(
        "fetch-spec-group-narrowed",
        "Fetching a spec whose destination is the least developed countries group with "
        "window=(2020, 2023) answers a new spec_id. The identity entry that asks Search for its "
        "sentence carries the spec's entity_groups whole, since a window leaves every role's "
        "binding as it was, and the sentence names the group.",
        _fetch_spec_grouped(window=NARROW_WINDOW),
    ),
    Recorded(
        "fetch-spec-group-origin-rebound",
        "Fetching a spec of Germany's flows to the least developed countries group with "
        "entities naming only origin rebinds origin to France. The identity entry that asks "
        "Search for the built sentence carries the destination's group, whose binding the "
        "build kept, and the sentence names the group.",
        _fetch_spec_grouped(entities={"origin": [REBOUND_ORIGIN]}),
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
        "operation-resume",
        "Resuming a long build another process started reads its operation by id, and the "
        "operation is pending or running. Waiting polls it at the server's pacing until done; "
        "the client holds no spec, so it takes the sentence from Search by posting the built "
        'identity with variable_text "dataset", then downloads the file. A resumed build holds '
        "no request, so a failed operation is not posted again.",
        _operation_resume,
    ),
    Recorded(
        "fetch-spec-frame-layout",
        'Fetching a spec with layout="frame" posts the frame rendering as the build\'s options '
        "and downloads the manifest's frame artifact, matched on format, bundle and its frame "
        "layout, with no Search call.",
        _fetch_spec_frame,
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
        "whose entity_ambiguous reason makes a choice gap, with one option per country: each "
        "option's data_query pins that country on the ambiguous wording.",
        _search_place(PLACE, 1),
    ),
    Recorded(
        "search-two-parts-place-ambiguous",
        "A two-part question naming a place that matches two countries answers no_data on both "
        "parts, each an entity_ambiguous choice gap with one option per country.",
        _search_place(TWO_PLACES, 2),
    ),
    Recorded(
        "choose-place-one-part",
        'Choosing "Congo [DRC]" by label in a one-part answer posts that option\'s data_query '
        "unchanged as the input of a new search, which answers definite.",
        _choose_place(PLACE, 1, None),
    ),
    Recorded(
        "choose-part-by-mention",
        'Naming the part by "Congo", a word its options mention, and the option by label posts '
        "that option's data_query, as naming the part by index does.",
        _choose_place(PLACE, 1, "Congo"),
    ),
    Recorded(
        "choose-place-two-parts",
        'Choosing "Congo [DRC]" on part 1 of a two-part answer posts dataset_resubmit with one '
        "entry per part in part order: the chosen option's data_query part as a parsed entry, "
        "and part 0, still a refusal, as a parsed entry of its restatement. Part 1 answers "
        "definite.",
        _choose_place(TWO_PLACES, 2, 1),
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
        "choose-option-beside-definite",
        "Choosing option 0 on part 1, a no_data part whose direction the question leaves "
        "open, beside a definite part whose places are a named group posts dataset_resubmit "
        "with the definite part's identity, entity_groups and assumptions as an identity "
        "entry, and the chosen option's data_query part as a parsed entry. Part 0 answers "
        "definite again, and part 1 answers the debt read in the chosen direction.",
        _choose_option_beside_definite,
    ),
    Recorded(
        "fetch-ref-newest-version",
        "The newest stored version of a spec is the one the versions route lists first, so "
        "the client asks for it alone with limit=1. The route needs no key.",
        _fetch_ref_newest_version,
    ),
    Recorded(
        "choose-candidate-echoes-assumptions",
        "Choosing candidate 0 on part 0 beside a definite part that states assumptions posts "
        "each identity entry with its reading's assumptions echoed: the chosen candidate's "
        "reading_assumptions entry, [] here, and the definite part's own assumptions. The "
        "rebuilt definite part states the same assumptions; an entry with no assumptions "
        "would be read as a fresh request.",
        _choose_candidate_echoes_assumptions,
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
    bodies: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class Synthetic:
    """A scenario derived from a recorded one, with no request. Its
    derivation runs over a context whose transport refuses every request."""

    name: str
    derived_from: str
    edit: str
    description: str
    derive: Callable[[dict[str, Any], Context], Derived]


def _same(parent: dict[str, Any], exchanges: list[dict[str, Any]]) -> Derived:
    return Derived(
        copy.deepcopy(parent["call"]), exchanges, copy.deepcopy(parent["expect"])
    )


def _waited(outcome: dict[str, Any], *refusals: dict[str, Any]) -> dict[str, Any]:
    """`outcome` with the waits before the retries that follow `refusals`,
    the refused exchanges of one request in order."""
    waits = [
        retry_wait(refused["response"].get("headers", {}), attempt)
        for attempt, refused in enumerate(refusals)
    ]
    return with_step_notes(outcome, long_builds=(), retry_waits=waits, warnings=())


def _refused_first(
    parent: dict[str, Any], index: int, refused: dict[str, Any]
) -> Derived:
    """The parent with `refused` answering exchange `index` first, and the
    retry's wait in the outcome of the last step, which makes it."""
    exchanges = copy.deepcopy(parent["exchanges"])
    exchanges.insert(index, refused)
    expect = copy.deepcopy(parent["expect"])
    expect[-1] = _waited(expect[-1], refused)
    return Derived(copy.deepcopy(parent["call"]), exchanges, expect)


_BUSY = _problem(
    "service_unavailable",
    503,
    "Service Unavailable",
    "The service is busy.",
    retryable=True,
)


def _retry_after_503(parent: dict[str, Any], ctx: Context) -> Derived:
    first = parent["exchanges"][0]
    refused = _refusal(first["request"], 503, _BUSY, {"retry-after": "1"})
    return _refused_first(parent, 0, refused)


def _proxy_429(parent: dict[str, Any], ctx: Context) -> Derived:
    first = parent["exchanges"][0]
    refused = _refusal(
        first["request"], 429, dict(_PROXY_RATE_LIMITED), {"retry-after": "2"}
    )
    return _refused_first(parent, 0, refused)


def _proxy_502(parent: dict[str, Any], ctx: Context) -> Derived:
    first = parent["exchanges"][0]
    refused = [
        _refusal(first["request"], 502, dict(_PROXY_UNAVAILABLE)) for _ in range(3)
    ]
    error = refusal_error(502, _PROXY_UNAVAILABLE, ctx.actions).outcome(
        ctx.client_codes
    )
    return Derived(
        copy.deepcopy(parent["call"]), refused, [_waited(error, *refused[:2])]
    )


def _build_index(parent: dict[str, Any]) -> int:
    return next(
        i
        for i, e in enumerate(parent["exchanges"])
        if e["request"]["method"] == "POST" and e["request"].get("path") == "/datasets"
    )


_TIMEOUT = _problem(
    "timeout",
    504,
    "Gateway Timeout",
    "The build passed its 120 s limit.",
    retryable=True,
)
_EXPIRED = _problem(
    "artifact_expired",
    410,
    "Gone",
    "Every listable artifact of this version aged out of the working tier unpinned.",
    retryable=False,
)
_NOT_RESOLVABLE = _problem(
    "member_not_resolvable",
    422,
    "Unprocessable Entity",
    "The spec's variable no longer resolves in the graph.",
    retryable=False,
)
_CAPABILITY_UNAVAILABLE = _problem(
    "capability_unavailable",
    501,
    "Not Implemented",
    "This deployment does not enable the frame layout.",
    retryable=False,
)
_OPERATION_FAILED = _problem(
    "operation_failed",
    500,
    "Internal Server Error",
    "The build's worker stopped before it finished.",
    retryable=True,
)
# The content version a rebuild answers when the data moved on.
NEWER_VERSION = "cv_" + "1" * 32
# The spec_id a ref's rebuild answers in `fetch-ref-expired-rebuild-other-spec`.
REBUILT_SPEC_ID = "spec_" + "2" * 32


def _retry_after_504(
    headers: Mapping[str, str] | None = None,
) -> Callable[[dict[str, Any], Context], Derived]:
    """A 504 timeout, carrying `headers`, answers the parent's first build."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        index = _build_index(parent)
        build = parent["exchanges"][index]["request"]
        return _refused_first(parent, index, _refusal(build, 504, _TIMEOUT, headers))

    return derive


def _ends_at_build(
    response: Callable[
        [dict[str, Any], Context], tuple[dict[str, Any], dict[str, Any]]
    ],
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's exchanges up to its first build, which `response` turns
    into the exchanges the build makes and the outcome the last step reaches."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        index = _build_index(parent)
        exchanges = copy.deepcopy(parent["exchanges"][:index])
        made, outcome = response(copy.deepcopy(parent["exchanges"][index]), ctx)
        return Derived(
            copy.deepcopy(parent["call"]),
            [*exchanges, *made],
            [*copy.deepcopy(parent["expect"][:-1]), outcome],
        )

    return derive


def _cut_off_twice(
    build: dict[str, Any], ctx: Context
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    refused = _refusal(build["request"], 504, _TIMEOUT)
    error = refusal_error(504, _TIMEOUT, ctx.actions).outcome(ctx.client_codes)
    return [refused, copy.deepcopy(refused)], _waited(error, refused)


def _cut_off_then_busy(
    build: dict[str, Any], ctx: Context
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A 504 cut-off, then a 503 on the one post after it, which raises."""
    cut_off = _refusal(build["request"], 504, _TIMEOUT)
    busy = _refusal(build["request"], 503, _BUSY)
    error = refusal_error(503, _BUSY, ctx.actions).outcome(ctx.client_codes)
    return [cut_off, busy], _waited(error, cut_off)


def _busy_then_cut_off(parent: dict[str, Any], ctx: Context) -> Derived:
    """A 503, then a 504 cut-off, answer the build before the recorded
    answer, and the retries' waits go in the outcome."""
    index = _build_index(parent)
    build = parent["exchanges"][index]["request"]
    refused = [_refusal(build, 503, _BUSY), _refusal(build, 504, _TIMEOUT)]
    exchanges = copy.deepcopy(parent["exchanges"])
    exchanges[index:index] = refused
    expect = copy.deepcopy(parent["expect"])
    expect[-1] = _waited(expect[-1], *refused)
    return Derived(copy.deepcopy(parent["call"]), exchanges, expect)


def _not_resolvable(
    build: dict[str, Any], ctx: Context
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    error = refusal_error(422, _NOT_RESOLVABLE, ctx.actions).outcome(ctx.client_codes)
    return [_refusal(build["request"], 422, _NOT_RESOLVABLE)], error


def _capability_unavailable(
    build: dict[str, Any], ctx: Context
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    problem = _CAPABILITY_UNAVAILABLE
    error = refusal_error(501, problem, ctx.actions).outcome(ctx.client_codes)
    return [_refusal(build["request"], 501, problem)], error


# The x-dfo-trace-id header of the responses `search-no-json-body` and
# `fetch-spec-strict-warning` raise on, which the errors carry.
READ_TRACE_ID = "3" * 32


def _strict_warning(parent: dict[str, Any], ctx: Context) -> Derived:
    """The fetch is strict, and its build's manifest, whose response carries
    READ_TRACE_ID, raises its first warning above info."""
    index = _build_index(parent)
    exchanges = copy.deepcopy(parent["exchanges"][: index + 1])
    response = exchanges[index]["response"]
    response.setdefault("headers", {})["x-dfo-trace-id"] = READ_TRACE_ID
    warning = data_warning(response["json"])
    require(warning is not None, "the manifest carries a warning above info")
    read_from(warning, trace_id(response["json"], response["headers"]))
    call = copy.deepcopy(parent["call"])
    call[-1]["strict"] = True
    return Derived(
        call,
        exchanges,
        [*copy.deepcopy(parent["expect"][:-1]), warning.outcome(ctx.client_codes)],
    )


def _digest_mismatch(name: str) -> Callable[[dict[str, Any], Context], Derived]:
    """The signed URL serves the scenario's own bytes, of another digest, on
    both downloads."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        exchanges = copy.deepcopy(parent["exchanges"])
        index = next(i for i, e in enumerate(exchanges) if "url" in e["request"])
        files, signed = exchanges[index - 1], exchanges[index]
        body_file = f"{name}.bin"
        signed["response"]["body_file"] = body_file
        exchanges[index + 1 : index + 1] = [copy.deepcopy(files), copy.deepcopy(signed)]
        return Derived(
            copy.deepcopy(parent["call"]),
            exchanges[: index + 3],
            [*copy.deepcopy(parent["expect"][:-1]), ctx.raised("digest_mismatch")],
            {body_file: b"Not the stored file.\n"},
        )

    return derive


def _resubmit_other_spec(parent: dict[str, Any], ctx: Context) -> Derived:
    """Search answers the built identity with the spec the fetch posted,
    which differs from the one the build answered."""
    index = next(
        i
        for i, e in enumerate(parent["exchanges"])
        if e["request"].get("path") == "/search/resolve"
    )
    exchanges = copy.deepcopy(parent["exchanges"][: index + 1])
    spec = exchanges[index]["response"]["json"]["parts"][0]["spec"]
    posted = parent["call"][-1]["spec"]["spec_id"]
    require(spec["spec_id"] != posted, "the build answered another spec_id")
    spec["spec_id"] = spec["definition"]["spec_id"] = posted
    error = ctx.raised("contract_violation", exchanges[index]["response"])
    return Derived(
        copy.deepcopy(parent["call"]),
        exchanges,
        [*copy.deepcopy(parent["expect"][:-1]), error],
    )


def _frame_several(parent: dict[str, Any], ctx: Context) -> Derived:
    require(len(parent["expect"][-1]["dataset"]["members"]) > 1, "several members")
    return Derived(
        [copy.deepcopy(parent["call"][0]), {"op": "fetch", "layout": "frame"}],
        copy.deepcopy(parent["exchanges"][:1]),
        [copy.deepcopy(parent["expect"][0]), ctx.raised("invalid_argument")],
    )


def _answer_ref(parent: dict[str, Any]) -> dict[str, Any]:
    """The ref the parent's fetch of a one-member answer writes."""
    build = parent["exchanges"][_build_index(parent)]
    manifest = build["response"]["json"]
    (member,) = parent["expect"][-1]["dataset"]["members"]
    artifact = pick_artifact(manifest, RENDERING)
    require(artifact is not None, "the manifest lists the long Parquet artifact")
    assert artifact is not None
    return {
        "format_version": 1,
        "members": [
            {
                "parts": member["parts"],
                "sentence": member["sentence"],
                "spec": {
                    "spec_id": member["spec_id"],
                    "definition": build["request"]["json"]["input"]["definition"],
                },
                "content_version": member["content_version"],
                "artifact": {
                    "digest": artifact["artifact_digest"],
                    "rendering": dict(RENDERING),
                },
            }
        ],
    }


def _snapshot_expired(ref: dict[str, Any]) -> dict[str, Any]:
    (member,) = ref["members"]
    path = f"/datasets/{member['spec']['spec_id']}/{member['content_version']}"
    return _refusal({"method": "GET", "path": path}, 410, _EXPIRED)


def _expired_rebuilt(parent: dict[str, Any], ctx: Context) -> Derived:
    """The ref the parent's fetch writes, its snapshot expired, and the
    parent's build, files and download as the rebuild."""
    ref = _answer_ref(parent)
    index = _build_index(parent)
    dataset = copy.deepcopy(parent["expect"][-1]["dataset"])
    del dataset["search_warning"]
    return Derived(
        [{"op": "fetch", "ref": ref}],
        [_snapshot_expired(ref), *copy.deepcopy(parent["exchanges"][index:])],
        [{"dataset": dataset}],
    )


def _expired_newer(parent: dict[str, Any], ctx: Context) -> Derived:
    """As `_expired_rebuilt`, with the rebuild answering a newer version."""
    ref = _answer_ref(parent)
    build = copy.deepcopy(parent["exchanges"][_build_index(parent)])
    build["response"]["json"]["content_version"] = NEWER_VERSION
    error = ClientError(
        FILE_EXPIRED,
        error=VERSION_UNAVAILABLE,
        newest_version=NEWER_VERSION,
        trace_id=_EXPIRED["trace_id"],
    )
    return Derived(
        [{"op": "fetch", "ref": ref}],
        [_snapshot_expired(ref), build],
        [error.outcome(ctx.client_codes)],
    )


def _expired_other_spec(parent: dict[str, Any], ctx: Context) -> Derived:
    """As `_expired_rebuilt`, with the rebuild's manifest naming
    REBUILT_SPEC_ID, which raises before any download."""
    ref = _answer_ref(parent)
    build = copy.deepcopy(parent["exchanges"][_build_index(parent)])
    build["response"]["json"]["definition"]["spec_id"] = REBUILT_SPEC_ID
    session = ctx.session("synthetic", canned=[_snapshot_expired(ref), build])
    session.fetch_ref(ref)
    require(not session.recorder.canned, "the lookup makes every exchange")
    require(
        _raises(session.expect[-1], "contract_violation", ctx),
        "the rebuild of another spec_id raises ContractError",
    )
    return Derived(session.call, session.recorder.exchanges, session.expect)


# The digest a ref names for a file the lookup never reaches.
UNREACHED_DIGEST = "sha256:" + "0" * 64


def _expired_not_reproducible(parent: dict[str, Any], ctx: Context) -> Derived:
    """A ref to the version the parent lists as newest, whose snapshot
    refuses 410 artifact_expired and whose rebuild refuses 422
    member_not_resolvable, then the parent's versions exchange."""
    (step,) = parent["call"]
    (outcome,) = parent["expect"]
    version = outcome["newest_version"]["content_version"]
    require(version is not None, "the parent lists a stored version")
    spec = step["spec"]
    ref = {
        "format_version": 1,
        "members": [
            {
                "parts": [],
                "sentence": spec["sentence"],
                "spec": {"spec_id": spec["spec_id"], "definition": spec["definition"]},
                "content_version": version,
                "artifact": {"digest": UNREACHED_DIGEST, "rendering": dict(RENDERING)},
            }
        ],
    }
    body = _build_body(_step_spec(spec), None, RENDERING)
    canned = [
        _snapshot_expired(ref),
        _refusal(
            {"method": "POST", "path": "/datasets", "json": body}, 422, _NOT_RESOLVABLE
        ),
        *copy.deepcopy(parent["exchanges"]),
    ]
    session = ctx.session("synthetic", canned=canned)
    session.fetch_ref(ref)
    require(not session.recorder.canned, "the lookup makes every exchange")
    return Derived(session.call, session.recorder.exchanges, session.expect)


def _expired_file_selector(parent: dict[str, Any], ctx: Context) -> Derived:
    """The files route refuses artifact_expired, and the file selector
    redirects to the parent's signed download."""
    exchanges = copy.deepcopy(parent["exchanges"])
    (member,) = parent["call"][0]["ref"]["members"]
    index = next(i for i, e in enumerate(exchanges) if "origin_path" in e["request"])
    files = exchanges[index]
    path = selector_path(
        member["spec"]["spec_id"],
        member["content_version"],
        member["artifact"]["rendering"],
    )
    selector = {
        "request": {"method": "GET", "path": path},
        "response": copy.deepcopy(files["response"]),
    }
    exchanges[index : index + 1] = [_refusal(files["request"], 410, _EXPIRED), selector]
    return _same(parent, exchanges)


def _poll_index(exchanges: Sequence[dict[str, Any]]) -> int:
    return next(
        i
        for i, e in enumerate(exchanges)
        if e["request"].get("path", "").startswith("/datasets/operations/")
    )


def _async_fetch(
    change: Callable[[list[dict[str, Any]], dict[str, Any]], None] | None = None,
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's mint and result as one fetch of its spec, which makes the
    same exchanges. `change` edits the exchanges and the fetch's outcome."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        mint, result = parent["call"]
        require(
            mint["op"] == "mint" and result["op"] == "result", "a mint, then result"
        )
        require(
            "retry_after_ms" in parent["exchanges"][0]["response"]["json"]["operation"],
            "the build's operation carries its pacing",
        )
        exchanges = copy.deepcopy(parent["exchanges"])
        outcome = copy.deepcopy(parent["expect"][1])
        if change is not None:
            change(exchanges, outcome["dataset"]["long_builds"][0])
        return Derived(
            [{"op": "fetch", "spec": copy.deepcopy(mint["spec"])}], exchanges, [outcome]
        )

    return derive


def _poll_rate_limited(exchanges: list[dict[str, Any]], build: dict[str, Any]) -> None:
    """A 429 with Retry-After 3 and no body answers the first poll."""
    index = _poll_index(exchanges)
    refused = {
        "request": copy.deepcopy(exchanges[index]["request"]),
        "response": {"status": 429, "headers": {"retry-after": "3"}},
    }
    exchanges.insert(index, refused)
    build["poll_waits"].insert(1, 3.0)


def _operation_failed(exchanges: list[dict[str, Any]], build: dict[str, Any]) -> None:
    """The first poll answers the operation failed with a retryable
    operation_failed, and the build is posted again and runs as recorded. The
    second build's operation is paced as the first's, so the waits repeat the
    first wait."""
    index = _poll_index(exchanges)
    failed = copy.deepcopy(exchanges[index])
    operation = failed["response"]["json"]["operation"]
    operation["status"] = "failed"
    operation.pop("progress", None)
    failed["response"]["json"] = {
        "operation": operation,
        "error": dict(_OPERATION_FAILED),
    }
    exchanges[index:index] = [failed, copy.deepcopy(exchanges[0])]
    build["poll_waits"].insert(0, build["poll_waits"][0])
    build["resubmissions"] = 1


def _poll_busy(exchanges: list[dict[str, Any]], build: dict[str, Any]) -> None:
    """A 503 answers the first poll, and two more answer the third, after
    the second answered. Each spends a retry, the answer between restores
    both, and each poll after a 503 waits the wait the 503's poll waited."""
    first = _poll_index(exchanges)
    poll = exchanges[first]["request"]
    exchanges.insert(first + 2, _refusal(poll, 503, _BUSY))
    exchanges.insert(first + 2, _refusal(poll, 503, _BUSY))
    exchanges.insert(first, _refusal(poll, 503, _BUSY))
    waits = build["poll_waits"]
    build["poll_waits"] = [waits[0], waits[0], waits[1], *[waits[2]] * 3, *waits[3:]]


def _unpaced(exchanges: list[dict[str, Any]]) -> None:
    """Every response loses its Retry-After and every operation resource its
    retry_after_ms, so the client paces its polls by its own backoff."""
    for exchange in exchanges:
        response = exchange["response"]
        response.get("headers", {}).pop("retry-after", None)
        body = response.get("json")
        if isinstance(body, dict) and isinstance(body.get("operation"), dict):
            body["operation"].pop("retry_after_ms", None)


def _poll_busy_unpaced(exchanges: list[dict[str, Any]], build: dict[str, Any]) -> None:
    """No response paces the polls, and a 503 with no Retry-After answers the
    second poll. Every poll waits the next step of the backoff, the one after
    the 503 included."""
    _unpaced(exchanges)
    first = _poll_index(exchanges)
    poll = exchanges[first]["request"]
    exchanges.insert(first + 1, _refusal(poll, 503, _BUSY))
    polls = sum(1 for e in exchanges if e["request"] == poll)
    build["poll_waits"] = [
        min(POLL_BACKOFF_FIRST * 2**step, POLL_BACKOFF_CAP) for step in range(polls)
    ]


# The Retry-After of the 503 that answers the first poll in
# `fetch-spec-poll-refused-retry-after`.
BUSY_POLL_RETRY_AFTER = 5


def _poll_busy_retry_after(
    exchanges: list[dict[str, Any]], build: dict[str, Any]
) -> None:
    """A 503 with Retry-After BUSY_POLL_RETRY_AFTER answers the first poll.
    The poll after it waits that long, and the polls after keep the server's
    pacing."""
    index = _poll_index(exchanges)
    headers = {"retry-after": str(BUSY_POLL_RETRY_AFTER)}
    exchanges.insert(index, _refusal(exchanges[index]["request"], 503, _BUSY, headers))
    build["poll_waits"].insert(1, float(BUSY_POLL_RETRY_AFTER))


_OPERATION_NOT_FOUND = _problem(
    "operation_not_found",
    404,
    "Not Found",
    "No operation has this id.",
    retryable=False,
)


# The codeless problem document of a 429 that says it is not retryable, which
# answers the first poll of `fetch-spec-poll-rate-limited-not-retryable`.
_RATE_LIMITED_FOR_GOOD = {
    "title": "Too Many Requests",
    "status": 429,
    "detail": "This key has used its requests for the day.",
    "trace_id": "0" * 32,
    "retryable": False,
}


def _poll_refused_at_once(
    status: int, body: dict[str, Any]
) -> Callable[[dict[str, Any], Context], Derived]:
    """A refusal of `status` with the not-retryable `body` answers the first
    poll, which raises it, and the exchanges after it go."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        derived = _async_fetch()(parent, ctx)
        index = _poll_index(derived.exchanges)
        refused = _refusal(derived.exchanges[index]["request"], status, body)
        (outcome,) = derived.expect
        (build,) = outcome["dataset"]["long_builds"]
        error = refusal_error(status, body, ctx.actions).outcome(ctx.client_codes)
        entry = {**build, "poll_waits": build["poll_waits"][:1]}
        return Derived(
            derived.call,
            [*derived.exchanges[:index], refused],
            [with_step_notes(error, long_builds=[entry], retry_waits=(), warnings=())],
        )

    return derive


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


def _signed_index(exchanges: Sequence[dict[str, Any]]) -> int:
    return next(i for i, e in enumerate(exchanges) if "url" in e["request"])


def _signed_fetch_partial(parent: dict[str, Any], ctx: Context) -> Derived:
    """The first signed fetch answers 206 with the file's bytes, and the files
    route is asked again."""
    exchanges = copy.deepcopy(parent["exchanges"])
    index = _signed_index(exchanges)
    files, signed = exchanges[index - 1], exchanges[index]
    partial = copy.deepcopy(signed)
    partial["response"]["status"] = 206
    exchanges[index:index] = [partial, copy.deepcopy(files)]
    return _same(parent, exchanges)


def _files_route_answers(
    status: int, *, keep_location: bool
) -> Callable[[dict[str, Any], Context], Derived]:
    """The files route answers `status`, with its location when
    `keep_location`, and raises ContractError unexpected_response. The
    exchanges after it go."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        index = _signed_index(parent["exchanges"]) - 1
        exchanges = copy.deepcopy(parent["exchanges"][: index + 1])
        response = exchanges[index]["response"]
        response["status"] = status
        dropped = {"content-type"} if keep_location else {"location", "content-type"}
        response["headers"] = {
            name: value
            for name, value in response.get("headers", {}).items()
            if name not in dropped
        }
        error = ctx.raised("unexpected_response", response)
        return Derived(
            copy.deepcopy(parent["call"]),
            exchanges,
            [*copy.deepcopy(parent["expect"][:-1]), error],
        )

    return derive


def _files_route_serves_file(parent: dict[str, Any], ctx: Context) -> Derived:
    """The files route answers 200 with the file the parent's signed URL
    served, and no signed fetch follows."""
    exchanges = copy.deepcopy(parent["exchanges"])
    index = _signed_index(exchanges)
    files, signed = exchanges[index - 1], exchanges[index]
    headers = {
        name: value
        for name, value in files["response"].get("headers", {}).items()
        if name != "location"
    }
    headers["content-type"] = signed["response"]["headers"]["content-type"]
    files["response"] = {**signed["response"], "headers": headers}
    del exchanges[index]
    return _same(parent, exchanges)


def _folded_parts_unnamed(parent: dict[str, Any], ctx: Context) -> Derived:
    """The two folded parts of the first member lose their variable_text,
    the first its member and the second to an empty string, so that member
    names no part and its part value is its sentence."""
    exchanges = copy.deepcopy(parent["exchanges"])
    parts = exchanges[0]["response"]["json"]["parts"]
    require(parts[0]["spec"]["spec_id"] == parts[1]["spec"]["spec_id"], "folded parts")
    del parts[0]["variable_text"]
    parts[1]["variable_text"] = ""
    derived = _same(parent, exchanges)
    dataset = derived.expect[-1]["dataset"]
    member = dataset["members"][0]
    member["parts"] = []
    dataset["part_rows"][0]["part"] = part_label(member)
    return derived


def _specs_an_object(response: dict[str, Any]) -> None:
    """Part 0's candidate specs become an object keyed by index."""
    candidates = response["json"]["parts"][0]["candidates"]
    candidates["specs"] = {str(i): s for i, s in enumerate(candidates["specs"])}


def _operations_an_object(parent: dict[str, Any], ctx: Context) -> Derived:
    """Part 0's operations become the one operation GROWTH, as an object."""
    exchanges = copy.deepcopy(parent["exchanges"])
    part = exchanges[0]["response"]["json"]["parts"][0]
    require(part.get("operations") == [], "part 0 has no operation")
    part["operations"] = dict(GROWTH)
    return _same(parent, exchanges)


def _artifact_unlisted(parent: dict[str, Any], ctx: Context) -> Derived:
    """The snapshot manifest lists no artifact of the ref's digest, and the
    file selector, in the ref member's rendering, redirects to the parent's
    signed download."""
    exchanges = copy.deepcopy(parent["exchanges"])
    (member,) = parent["call"][0]["ref"]["members"]
    manifest = exchanges[0]["response"]["json"]
    digest = member["artifact"]["digest"]
    manifest["artifacts"] = [
        a for a in manifest["artifacts"] if a["artifact_digest"] != digest
    ]
    index = next(i for i, e in enumerate(exchanges) if "origin_path" in e["request"])
    path = selector_path(
        member["spec"]["spec_id"],
        member["content_version"],
        member["artifact"]["rendering"],
    )
    exchanges[index]["request"] = {"method": "GET", "path": path}
    return _same(parent, exchanges)


def _deferred(body: dict[str, Any]) -> None:
    body["parts"][1]["status"] = "deferred"


def _unknown_enum_value(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    exchanges[0]["response"]["json"]["parts"][0]["no_data"]["category"] = "undetermined"
    return _same(parent, exchanges)


def _null_member(parent: dict[str, Any], ctx: Context) -> Derived:
    """Flips the status column's `distinct`, an optional member that admits
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
    error = ctx.raised("contract_violation", exchanges[index]["response"])
    return Derived(
        copy.deepcopy(parent["call"]), exchanges, [parent["expect"][0], error]
    )


type BodyEdit = Callable[[Any, Context], Any]


def _choice(
    option: int | str,
    part: int | str | None = None,
    *,
    edit: BodyEdit | None = None,
    headers: Mapping[str, str] | None = None,
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's search, its answer body changed by `edit` and its
    response carrying `headers` too, then a choice that refuses and makes no
    request."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        canned = copy.deepcopy(parent["exchanges"][:1])
        response = canned[0]["response"]
        if headers is not None:
            response.setdefault("headers", {}).update(headers)
        if edit is not None:
            response["json"] = edit(response["json"], ctx)
        session = ctx.session("synthetic", canned=canned)
        answer = session.search(parent["call"][0]["question"])
        session.choose(answer, option, part=part)
        return Derived(session.call, session.recorder.exchanges, session.expect)

    return derive


def _without_options(body: Any, ctx: Context) -> Any:
    """Each no_data part's options go, as from a build that serves none."""
    body = copy.deepcopy(body)
    for part in body["parts"]:
        part.get("no_data", {}).pop("options", None)
    return body


def _raises(outcome: Mapping[str, Any], code: str, ctx: Context) -> bool:
    """Whether `outcome` is the error a client raises itself with `code`,
    whatever trace id it carries."""
    error = outcome.get("error", {})
    wanted = ctx.raised(code)["error"]["class"]
    return (error.get("class"), error.get("code")) == (wanted, code)


def _choice_refused(
    option: int | str, part: int | str | None = None, *, edit: BodyEdit | None = None
) -> Callable[[dict[str, Any], Context], Derived]:
    choose = _choice(option, part, edit=edit)

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
    headers: Mapping[str, str] | None = None,
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent with its first answer body changed, and that answer's
    outcome derived again. With `headers`, the answer's response carries
    them too, and the parent's steps run again over the changed exchanges,
    so every outcome that reads the answer follows from them."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        exchanges = copy.deepcopy(parent["exchanges"])
        body = exchanges[0]["response"]["json"]
        change(body)
        if headers is not None:
            exchanges[0]["response"].setdefault("headers", {}).update(headers)
            session = ctx.session("synthetic", canned=exchanges)
            drive(session, parent["call"])
            require(not session.recorder.canned, "the steps make every exchange")
            return Derived(session.call, session.recorder.exchanges, session.expect)
        expect = copy.deepcopy(parent["expect"])
        expect[0] = answer_outcome(answer_from_body(body), gap_kinds(ctx.actions))
        return Derived(copy.deepcopy(parent["call"]), exchanges, expect)

    return derive


# The x-dfo-trace-id header the Search responses of `answer-timed-out` and
# `choose-refused-shared-label` carry. The first's answer_incomplete carries
# it, and the second's choice_invalid, which describes the caller's choice,
# does not.
ANSWER_TRACE_ID = "5" * 32


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
    error = ctx.raised("contract_violation", exchanges[0]["response"])
    return Derived(copy.deepcopy(parent["call"]), exchanges, [error])


def _partial_nothing_answered(parent: dict[str, Any], ctx: Context) -> Derived:
    require(parent["expect"][0]["answer"]["specs"] == [], "no part has a reading")
    (search,) = parent["exchanges"]
    return Derived(
        [*copy.deepcopy(parent["call"]), {"op": "fetch", "partial": True}],
        copy.deepcopy(parent["exchanges"]),
        [
            *copy.deepcopy(parent["expect"]),
            ctx.raised("answer_incomplete", search["response"]),
        ],
    )


_INFO_NOTICE = {
    "code": "retrieval_score_weak",
    "severity": "info",
    "message": (
        "the chosen reading's match to the query is weak; treat it with less "
        "confidence than a strong match."
    ),
}


# A declared default as a recorded definite part states it, the item
# `choose-candidate-echoes-assumptions` records on its definite part.
_DECLARED_DEFAULT = {
    "kind": "declared_default",
    "binding_ref": "how:dac2aAidType",
    "text": "official development assistance",
    "refs": ["DAC2aAidType206"],
}


def _assumption_clause(part_index: int, texts: Sequence[str]) -> list[dict[str, Any]]:
    """The sentence plan tokens Search renders for a part's assumptions:
    one `segment="assumption"` clause, "Assumed: a; b."."""

    def token(text: str) -> dict[str, Any]:
        return {
            "kind": "text",
            "text": text,
            "segment": "assumption",
            "entity_refs": [],
            "states": [],
            "part_index": part_index,
        }

    clause = [token(" "), token("Assumed: ")]
    for index, text in enumerate(texts):
        clause.extend([token("; "), token(text)] if index else [token(text)])
    return [*clause, token(".")]


def _picked_reading_assumes(parent: dict[str, Any], ctx: Context) -> Derived:
    """The parent's search and pick, with the picked reading's
    reading_assumptions entry holding one declared default. The resubmit
    echoes it unchanged, and Search's answer states it."""
    exchanges = copy.deepcopy(parent["exchanges"][:2])
    search, resubmit = exchanges
    part = search["response"]["json"]["parts"][0]
    spec_id = part["candidates"]["specs"][0]["spec_id"]
    require(part["reading_assumptions"][spec_id] == [], "the reading assumes nothing")
    part["reading_assumptions"][spec_id] = [copy.deepcopy(_DECLARED_DEFAULT)]
    (entry,) = resubmit["request"]["json"]["input"]["entries"]
    entry["assumptions"] = [copy.deepcopy(_DECLARED_DEFAULT)]
    body = resubmit["response"]["json"]
    body["parts"][0]["assumptions"] = [copy.deepcopy(_DECLARED_DEFAULT)]
    clause = _assumption_clause(0, [_DECLARED_DEFAULT["text"]])
    body["sentence_plan"].extend(clause)
    body["rendered_sentence"] += "".join(token["text"] for token in clause)
    answer = answer_from_body(search["response"]["json"])
    kinds = gap_kinds(ctx.actions)
    posted = choice_input(answer, choice_for(answer, kinds, None, 0))
    require(posted == resubmit["request"]["json"]["input"], "the pick echoes the list")
    return Derived(
        copy.deepcopy(parent["call"][:2]),
        exchanges,
        [
            answer_outcome(answer, kinds),
            answer_outcome(answer_from_body(body), kinds),
        ],
    )


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


def _signed_url_refused_twice(parent: dict[str, Any], ctx: Context) -> Derived:
    """Both signed fetches refuse 403, the second after the files route was
    asked again."""
    exchanges = copy.deepcopy(parent["exchanges"])
    index = next(i for i, e in enumerate(exchanges) if "url" in e["request"])
    files, signed = exchanges[index - 1], exchanges[index]
    signed["response"] = {"status": 403, "headers": {"content-type": "application/xml"}}
    return Derived(
        copy.deepcopy(parent["call"]),
        [*exchanges[: index + 1], copy.deepcopy(files), copy.deepcopy(signed)],
        [*copy.deepcopy(parent["expect"][:-1]), ctx.raised("download_failed")],
    )


# The relative Location a files route answers in `fetch-spec-relative-location`,
# which names a path beside the route's own.
RELATIVE_LOCATION = "bytes"


def _relative_location(parent: dict[str, Any], ctx: Context) -> Derived:
    """The files route answers RELATIVE_LOCATION, and the file comes from the
    path it resolves to against the route's URL, on the API's origin."""
    exchanges = copy.deepcopy(parent["exchanges"])
    index = next(i for i, e in enumerate(exchanges) if "url" in e["request"])
    files, signed = exchanges[index - 1], exchanges[index]
    route = files["request"]["origin_path"]
    files["response"]["headers"]["location"] = RELATIVE_LOCATION
    resolved = urlsplit(urljoin(f"https://api.invalid{route}", RELATIVE_LOCATION))
    signed["request"] = {"method": "GET", "origin_path": resolved.path}
    return _same(parent, exchanges)


def _failed_without_error(parent: dict[str, Any], ctx: Context) -> Derived:
    """The resumed operation's first poll answers it failed with no error."""
    resume, poll = copy.deepcopy(parent["exchanges"][:2])
    operation = poll["response"]["json"]["operation"]
    operation["status"] = "failed"
    operation.pop("progress", None)
    poll["response"]["json"] = {"operation": operation}
    error, _ = failed_operation(
        poll["response"]["json"], ctx.actions, poll["response"].get("headers")
    )
    (build,) = parent["expect"][1]["dataset"]["long_builds"]
    entry = {**build, "poll_waits": build["poll_waits"][:1], "resubmissions": 0}
    outcome = with_step_notes(
        error.outcome(ctx.client_codes),
        long_builds=[entry],
        retry_waits=(),
        warnings=(),
    )
    return Derived(
        copy.deepcopy(parent["call"]),
        [resume, poll],
        [copy.deepcopy(parent["expect"][0]), outcome],
    )


def _versions_renamed(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    body = exchanges[0]["response"]["json"]
    body["items"] = body.pop("versions")
    error = ctx.raised("contract_violation", exchanges[0]["response"])
    return Derived(copy.deepcopy(parent["call"]), exchanges, [error])


# The Sunset header `search-sunset-warning`'s responses carry, with no
# Deprecation header beside it.
SUNSET = "Sat, 01 May 2027 00:00:00 GMT"


def _sunset_twice(parent: dict[str, Any], ctx: Context) -> Derived:
    """The parent's search made twice, each response carrying SUNSET. The
    first warns, and the second, with the same value, does not."""
    exchange = copy.deepcopy(parent["exchanges"][0])
    exchange["response"]["headers"]["sunset"] = SUNSET
    (step,) = parent["call"]
    (outcome,) = parent["expect"]
    warned = with_step_notes(
        copy.deepcopy(outcome),
        long_builds=(),
        retry_waits=(),
        warnings=header_warnings(exchange["response"]["headers"], set()),
    )
    return Derived(
        [copy.deepcopy(step), copy.deepcopy(step)],
        [exchange, copy.deepcopy(exchange)],
        [warned, copy.deepcopy(outcome)],
    )


# The origin of `fetch-spec-foreign-artifact-url`'s artifact urls, neither the
# API's nor the storage host's.
FOREIGN_ORIGIN = "https://downloads.example.com"


def _artifact_urls_on(
    origin: Callable[[dict[str, Any]], str],
) -> Callable[[dict[str, Any], Context], Derived]:
    """Every artifact `url` of the parent's build becomes absolute on the
    origin `origin` gives for the parent. On the API's own origin the record
    stands, and on any other the build raises ContractError and the
    exchanges after it go."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        exchanges = copy.deepcopy(parent["exchanges"])
        index = _build_index(parent)
        at = origin(parent)
        for artifact in exchanges[index]["response"]["json"]["artifacts"]:
            artifact["url"] = at + artifact["url"]
        if at == _origin(parent["origin"]["base_url"]):
            return _same(parent, exchanges)
        error = ctx.raised("contract_violation", exchanges[index]["response"])
        return Derived(
            copy.deepcopy(parent["call"]),
            exchanges[: index + 1],
            [*copy.deepcopy(parent["expect"][:-1]), error],
        )

    return derive


# The x-dfo-warning messages and the Deprecation value
# `search-server-warning`'s responses carry.
SERVER_MESSAGE = "Search runs on a reduced index while it is rebuilt."
OTHER_SERVER_MESSAGE = "Search answers from a cached release."
DEPRECATION = "@1777593600"


def _server_warnings(parent: dict[str, Any], ctx: Context) -> Derived:
    """The parent's search made three times. The first response carries
    SERVER_MESSAGE, the second the same message and SUNSET, and the third
    OTHER_SERVER_MESSAGE, DEPRECATION and SUNSET. The warnings each step
    issues are written out, so a change to the warning rules fails the
    record."""
    (step,) = parent["call"]
    (outcome,) = parent["expect"]
    exchange = parent["exchanges"][0]
    carried = [
        {"x-dfo-warning": SERVER_MESSAGE},
        {"x-dfo-warning": SERVER_MESSAGE, "sunset": SUNSET},
        {
            "x-dfo-warning": OTHER_SERVER_MESSAGE,
            "deprecation": DEPRECATION,
            "sunset": SUNSET,
        },
    ]
    issued = [
        ["server_warning"],
        ["route_deprecated"],
        ["route_deprecated", "server_warning"],
    ]
    exchanges = []
    for headers in carried:
        made = copy.deepcopy(exchange)
        made["response"]["headers"].update(headers)
        exchanges.append(made)
    expect = [
        with_step_notes(
            copy.deepcopy(outcome), long_builds=(), retry_waits=(), warnings=codes
        )
        for codes in issued
    ]
    return Derived([copy.deepcopy(step) for _ in carried], exchanges, expect)


def _search_ends(
    change: Callable[[dict[str, Any]], None], code: str
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's search alone, its response changed by `change`, ending
    in the client error `code`."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        exchange = copy.deepcopy(parent["exchanges"][0])
        change(exchange["response"])
        error = ctx.raised(code, exchange["response"])
        return Derived(copy.deepcopy(parent["call"][:1]), [exchange], [error])

    return derive


def _status_a_number(response: dict[str, Any]) -> None:
    response["json"]["parts"][1]["status"] = 7


def _definition_spec_id_differs(response: dict[str, Any]) -> None:
    response["json"]["parts"][0]["spec"]["definition"]["spec_id"] = UNKNOWN_SPEC_ID


# The spec_id a Search spec's definition names in
# `answer-definition-spec-id-mismatch`, one no build answered.
UNKNOWN_SPEC_ID = "spec_" + "0" * 32


def _without_json(response: dict[str, Any]) -> None:
    del response["json"]
    response["headers"] = {
        "content-type": "text/html; charset=utf-8",
        "x-dfo-trace-id": READ_TRACE_ID,
    }


def _without_data_query(body: Any, ctx: Context) -> Any:
    """The first option of the one no_data part loses its data_query."""
    body = copy.deepcopy(body)
    del body["parts"][0]["no_data"]["options"][0]["data_query"]
    return body


def _choice_without_data_query(parent: dict[str, Any], ctx: Context) -> Derived:
    derived = _choice(parent["call"][1]["option"], edit=_without_data_query)(
        parent, ctx
    )
    require(
        _raises(derived.expect[-1], "contract_violation", ctx),
        "the choice raises ContractError",
    )
    return derived


def _choice_posted(edit: BodyEdit) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's search, its answer body changed by `edit`, then its
    choice, which posts the input the rules give for the changed answer and
    takes the parent's recorded response to it."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        search, choose = parent["call"]
        canned = copy.deepcopy(parent["exchanges"][:2])
        body = edit(canned[0]["response"]["json"], ctx)
        canned[0]["response"]["json"] = body
        answer = answer_from_body(body)
        choice = choice_for(
            answer, gap_kinds(ctx.actions), choose.get("part"), choose["option"]
        )
        canned[1]["request"]["json"] = search_body(choice_input(answer, choice))
        session = ctx.session("synthetic", canned=canned)
        answered = session.search(search["question"])
        session.choose(answered, choose["option"], part=choose.get("part"))
        return Derived(session.call, session.recorder.exchanges, session.expect)

    return derive


# The operation `choose-echoes-separate-and-operations`'s definite part asks for.
GROWTH = {"verb": "growth", "phrase": "growth of GDP", "over": "year"}


def _separate_with_operation(body: Any, ctx: Context) -> Any:
    """Part 0, the definite one, is asked beside the others and asks for
    GROWTH."""
    body = copy.deepcopy(body)
    body["parts"][0].update(separate=True, operations=[dict(GROWTH)])
    return body


def _definite_unsupported(body: Any, ctx: Context) -> Any:
    """Part 0, the definite one, becomes an unsupported part with no
    restatement."""
    body = copy.deepcopy(body)
    part = body["parts"][0]
    for member in ("spec", "restatement", "assumptions"):
        part.pop(member, None)
    part["status"] = "unsupported"
    part["unsupported"] = {
        "code": "scope_too_broad",
        "pointer": "/definition/identity",
        "detail": "the family is too broad to settle; bind a what or how slot",
    }
    return body


def _definite_deferred(body: Any, ctx: Context) -> Any:
    """Part 0 takes the status "deferred", which no client knows, and loses
    its restatement."""
    body = copy.deepcopy(body)
    body["parts"][0]["status"] = "deferred"
    body["parts"][0].pop("restatement", None)
    return body


def _definite_unnamed(body: Any, ctx: Context) -> Any:
    """Part 0, the definite one, loses its variable_text."""
    body = copy.deepcopy(body)
    del body["parts"][0]["variable_text"]
    return body


def _labels_shared(body: Any, ctx: Context) -> Any:
    """The no_data part's second option takes the first one's label."""
    body = copy.deepcopy(body)
    options = body["parts"][0]["no_data"]["options"]
    options[1]["label"] = options[0]["label"]
    return body


def _first_candidate_label(body: Any) -> str:
    """The label of the first candidate of the answer's first part, which no
    other candidate of that part shares."""
    specs = body["parts"][0]["candidates"]["specs"]
    labels = [candidate_label(spec) for spec in specs]
    require(
        labels[0] is not None and labels.count(labels[0]) == 1,
        "the first candidate's label is its own",
    )
    assert labels[0] is not None
    return labels[0]


def _candidate_by_label(parent: dict[str, Any], ctx: Context) -> Derived:
    """The parent's search and its choice of candidate 0, named by its label,
    which posts what the index posts and reaches the same answer."""
    search, choose = parent["call"][:2]
    require(choose.get("option") == 0, "the parent chooses candidate 0")
    label = _first_candidate_label(parent["exchanges"][0]["response"]["json"])
    session = ctx.session("synthetic", canned=copy.deepcopy(parent["exchanges"][:2]))
    answer = session.search(search["question"])
    session.choose(answer, label, part=choose.get("part"))
    require(not session.recorder.canned, "the choice makes the parent's request")
    require(
        session.expect == parent["expect"][:2],
        "the label reaches the answer the index reaches",
    )
    return Derived(session.call, session.recorder.exchanges, session.expect)


def _candidate_label_shared(parent: dict[str, Any], ctx: Context) -> Derived:
    """The second candidate takes the first one's sentence plan, so both have
    the first one's label, and the choice names that label."""

    def edit(body: Any, ctx: Context) -> Any:
        body = copy.deepcopy(body)
        first, second = body["parts"][0]["candidates"]["specs"][:2]
        plan = first["resolved"]["sentence_plan"]
        second["resolved"]["sentence_plan"] = copy.deepcopy(plan)
        return body

    label = _first_candidate_label(parent["exchanges"][0]["response"]["json"])
    return _choice_refused(label, edit=edit)(parent, ctx)


def _malformed_echo_items(body: Any, ctx: Context) -> Any:
    """The definite part's operations hold 42 before GROWTH and its
    assumptions end with a string, and the chosen candidate's
    reading_assumptions end with 7."""
    body = copy.deepcopy(body)
    candidates, definite = body["parts"]
    spec_id = candidates["candidates"]["specs"][0]["spec_id"]
    candidates["reading_assumptions"][spec_id].append(7)
    definite["operations"] = [42, dict(GROWTH)]
    definite["assumptions"].append("assumed")
    return body


def _echo_drops_malformed(parent: dict[str, Any], ctx: Context) -> Derived:
    """The parent's choice over `_malformed_echo_items`' answer, which posts
    only the object items of each list it echoes."""
    derived = _choice_posted(_malformed_echo_items)(parent, ctx)
    body = parent["exchanges"][0]["response"]["json"]
    candidates, definite = body["parts"]
    spec_id = candidates["candidates"]["specs"][0]["spec_id"]
    chosen, beside = derived.exchanges[1]["request"]["json"]["input"]["entries"]
    require(
        chosen["assumptions"] == candidates["reading_assumptions"][spec_id]
        and beside["operations"] == [GROWTH]
        and beside["assumptions"] == definite["assumptions"],
        "the echo drops each item that is not an object",
    )
    return derived


def _two_part_query(body: Any, ctx: Context) -> Any:
    """The first option's data_query holds its one part twice."""
    body = copy.deepcopy(body)
    query = body["parts"][0]["no_data"]["options"][0]["data_query"]
    query["parts"] = [query["parts"][0], copy.deepcopy(query["parts"][0])]
    return body


def _choice_raises(
    code: str, edit: BodyEdit, headers: Mapping[str, str] | None = None
) -> Callable[[dict[str, Any], Context], Derived]:
    """The parent's choice over its answer changed by `edit`, its Search
    response carrying `headers` too, which raises the client code `code`
    with no request."""

    def derive(parent: dict[str, Any], ctx: Context) -> Derived:
        choose = parent["call"][1]
        derived = _choice(
            choose["option"], choose.get("part"), edit=edit, headers=headers
        )(parent, ctx)
        require(_raises(derived.expect[-1], code, ctx), f"the choice raises {code}")
        return derived

    return derive


def _not_an_object(response: dict[str, Any]) -> None:
    response["json"] = [response["json"]]


def _no_parts(response: dict[str, Any]) -> None:
    response["json"]["parts"] = []


def _spec_without(member: str) -> Callable[[dict[str, Any]], None]:
    """Part 0's spec loses `member`, or its definition loses it when the
    member is `identity`."""

    def change(response: dict[str, Any]) -> None:
        spec = response["json"]["parts"][0]["spec"]
        (spec["definition"] if member == "identity" else spec).pop(member)

    return change


def _manifest_without_identity(parent: dict[str, Any], ctx: Context) -> Derived:
    index = _build_index(parent)
    exchanges = copy.deepcopy(parent["exchanges"][: index + 1])
    del exchanges[index]["response"]["json"]["definition"]["identity"]
    error = ctx.raised("contract_violation", exchanges[index]["response"])
    return Derived(
        copy.deepcopy(parent["call"]),
        exchanges,
        [*copy.deepcopy(parent["expect"][:-1]), error],
    )


def _nested_specs_unchecked(parent: dict[str, Any], ctx: Context) -> Derived:
    """Each position that holds a Spec no client reads gets the chosen
    answer's spec without resolved and without its definition's identity:
    the first answer's no_data flagged_match and alternate_match, and the
    chosen spec's resolved.equivalent_readings."""
    exchanges = copy.deepcopy(parent["exchanges"])
    asked, chosen = (e["response"]["json"] for e in exchanges[:2])
    no_data = next(p["no_data"] for p in asked["parts"] if p["status"] == "no_data")
    spec = chosen["parts"][0]["spec"]
    bare = copy.deepcopy(spec)
    del bare["resolved"], bare["definition"]["identity"]
    no_data["flagged_match"] = copy.deepcopy(bare)
    no_data["alternate_match"] = copy.deepcopy(bare)
    spec["resolved"]["equivalent_readings"] = [bare]
    return _same(parent, exchanges)


# The trace id the codeless problem document of `search-refused-without-code`
# carries, and the x-dfo-trace-id header it and `search-unauthorized` carry.
BODY_TRACE_ID = "1" * 32
HEADER_TRACE_ID = "2" * 32


def _refused_without_code(parent: dict[str, Any], ctx: Context) -> Derived:
    """A 503 problem document with no code, retryable false and a trace id
    answers the search, which raises it unretried."""
    first = parent["exchanges"][0]
    body = {
        "title": "Service Unavailable",
        "status": 503,
        "detail": "The service is busy.",
        "trace_id": BODY_TRACE_ID,
        "retryable": False,
    }
    refused = _refusal(first["request"], 503, body)
    refused["response"]["headers"]["x-dfo-trace-id"] = HEADER_TRACE_ID
    error = refusal_error(
        503, body, ctx.actions, refused["response"]["headers"]
    ).outcome(ctx.client_codes)
    return Derived(copy.deepcopy(parent["call"]), [refused], [error])


def _unauthorized(parent: dict[str, Any], ctx: Context) -> Derived:
    """A bodiless 401 with a trace id header answers the search. The
    replay's key does not change when the client resolves it again, so the
    client sends nothing more, and the error's trace id is the header's."""
    first = parent["exchanges"][0]
    headers = {"www-authenticate": "Bearer", "x-dfo-trace-id": HEADER_TRACE_ID}
    refused = {
        "request": copy.deepcopy(first["request"]),
        "response": {"status": 401, "headers": headers},
    }
    error = refusal_error(401, None, ctx.actions, headers).outcome(ctx.client_codes)
    return Derived(copy.deepcopy(parent["call"]), [refused], [error])


def _empty_narrowing(parent: dict[str, Any], ctx: Context) -> Derived:
    """The fetch names `entities: {}` and `facets: {}`, which narrow nothing."""
    derived = _same(parent, copy.deepcopy(parent["exchanges"]))
    derived.call[0].update(entities={}, facets={})
    return derived


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
        "client waits the first backoff, 0.5 seconds, builds once more and reaches the same "
        "dataset.",
        _retry_after_504(),
    ),
    Synthetic(
        "fetch-spec-504-retry-after",
        "fetch-spec-known-sentence",
        "A 504 timeout with retryable true and Retry-After 5 answers the build.",
        "A build cut off by a 504 that carries Retry-After is posted once more after the "
        "Retry-After seconds, not the backoff, and reaches the same dataset.",
        _retry_after_504({"retry-after": "5"}),
    ),
    Synthetic(
        "fetch-spec-504-twice",
        "fetch-spec-known-sentence",
        "A 504 timeout with retryable true answers the build and its one retry.",
        "A synchronous build cut off twice is retried once only: the second 504 raises the "
        "timeout code's ServerError.",
        _ends_at_build(_cut_off_twice),
    ),
    Synthetic(
        "fetch-spec-504-then-503",
        "fetch-spec-known-sentence",
        "A 504 timeout answers the build, and a 503 service_unavailable its one retry.",
        "After a 504 cut-off the build is posted once more only, so the 503 that answers "
        "that post raises its ServerError although one retry of the two is left.",
        _ends_at_build(_cut_off_then_busy),
    ),
    Synthetic(
        "fetch-spec-503-then-504",
        "fetch-spec-known-sentence",
        "A 503 service_unavailable answers the build, and a 504 timeout its first retry.",
        "A 504 that cuts off the first retry is the first cut-off, so the build is posted "
        "once more, the second of its two retries, after waits of 0.5 and 1 seconds, and "
        "reaches the same dataset.",
        _busy_then_cut_off,
    ),
    Synthetic(
        "fetch-spec-member-not-resolvable",
        "fetch-spec-known-sentence",
        "A 422 member_not_resolvable answers the build.",
        "A build refused because the spec no longer resolves raises InvalidRequestError with "
        "the server's code, and makes no further request.",
        _ends_at_build(_not_resolvable),
    ),
    Synthetic(
        "fetch-spec-capability-unavailable",
        "fetch-spec-frame-layout",
        "A 501 capability_unavailable answers the build.",
        "A well-formed build that asks for a rendering this deployment does not enable "
        "refuses 501 capability_unavailable, which raises InvalidRequestError, since the "
        "caller asks for another rendering, and makes no further request.",
        _ends_at_build(_capability_unavailable),
    ),
    Synthetic(
        "fetch-spec-strict-warning",
        "fetch-spec-known-sentence",
        f"The fetch is strict, the build's response carries x-dfo-trace-id {READ_TRACE_ID}, "
        "and the exchanges after the build go.",
        "A strict fetch raises the manifest's first warning above info severity, "
        "coverage_below_scope at warn here, as DataWarning before the download, with the "
        "trace id of the build response it read.",
        _strict_warning,
    ),
    Synthetic(
        "fetch-spec-digest-mismatch",
        "fetch-spec-known-sentence",
        "The signed download serves other bytes, and the files route is asked once more for the same.",
        "Bytes that do not hash to the artifact's digest ask the files route for a fresh "
        "redirect once; a second mismatch raises ServerError digest_mismatch.",
        _digest_mismatch("fetch-spec-digest-mismatch"),
    ),
    Synthetic(
        "fetch-spec-resubmit-other-spec",
        "fetch-spec-narrowed",
        "Search answers the built identity with the posted spec_id, and the exchanges after it go.",
        "When Search answers the built identity with a spec_id other than the built one, the "
        "client raises ContractError before the download.",
        _resubmit_other_spec,
    ),
    Synthetic(
        "fetch-frame-several-readings",
        "answer-several-readings-folded",
        'The fetch of the answer asks for layout "frame", and the exchanges after the search go.',
        "The frame layout holds one reading, so fetching an answer with two members in it "
        "raises InvalidRequestError before any build.",
        _frame_several,
    ),
    Synthetic(
        "fetch-ref-expired-rebuild-same-version",
        "answer-definite-fetch",
        "A ref to the fetched member, whose snapshot route refuses 410 artifact_expired; the parent's build, files and download follow.",
        "A ref whose snapshot refuses artifact_expired builds its spec again with the ref's "
        "rendering; the build answers the same content_version, so the client downloads it "
        "and returns the ref's member, its parts and sentence kept, with no Search call.",
        _expired_rebuilt,
    ),
    Synthetic(
        "fetch-ref-expired-newer-version",
        "answer-definite-fetch",
        f"As fetch-ref-expired-rebuild-same-version, with the rebuild answering {NEWER_VERSION}.",
        "A rebuild that answers another content_version cannot return the stored one: the "
        "client raises VersionUnavailableError with the snapshot's artifact_expired code and "
        "the rebuilt version as the newest, and downloads nothing.",
        _expired_newer,
    ),
    Synthetic(
        "fetch-ref-expired-rebuild-other-spec",
        "answer-definite-fetch",
        f"As fetch-ref-expired-rebuild-same-version, with the rebuild's manifest naming {REBUILT_SPEC_ID}.",
        "A rebuild of a ref's spec must answer the ref's spec_id as well as its "
        "content_version: a manifest of another spec_id raises ContractError "
        "contract_violation, as a snapshot of another spec_id does, and the client "
        "downloads nothing.",
        _expired_other_spec,
    ),
    Synthetic(
        "fetch-ref-expired-not-reproducible",
        "fetch-ref-newest-version",
        "A fetch of a ref to the listed version, its unreached artifact digest all zeros, "
        "whose snapshot refuses 410 artifact_expired and whose rebuild refuses 422 "
        "member_not_resolvable, comes before the parent's versions exchange.",
        "When the stored version has expired and the build is refused, the client asks the "
        "versions route for the newest version alone with limit=1, and raises "
        "VersionUnavailableError with the snapshot's artifact_expired code and that version "
        "as the newest.",
        _expired_not_reproducible,
    ),
    Synthetic(
        "fetch-ref-expired-file-selector",
        "fetch-ref",
        "The files route refuses 410 artifact_expired, and the file selector redirects to the parent's signed download.",
        "When the files route refuses artifact_expired, the client asks the file selector for "
        "the stored version in the ref's rendering and downloads the file it redirects to, "
        "which hashes to the stored digest.",
        _expired_file_selector,
    ),
    Synthetic(
        "fetch-ref-artifact-unlisted",
        "fetch-ref",
        "The snapshot manifest's artifacts no longer list the ref's digest, and the file "
        "selector redirects to the parent's signed download.",
        "A ref member whose digest the snapshot manifest does not list asks the file "
        "selector in the ref member's own rendering, and bytes that hash to the stored "
        "digest give the dataset.",
        _artifact_unlisted,
    ),
    Synthetic(
        "fetch-spec-async-build",
        "mint-async-build",
        "The mint and result steps become one fetch of the spec, which makes the same exchanges.",
        "Fetching a spec whose build answers 202 polls the operation: it waits the 202's "
        "retry_after_ms, then each poll's, backing off from 1 to 8 seconds when a poll gives "
        "none, and downloads the done manifest's file.",
        _async_fetch(),
    ),
    Synthetic(
        "fetch-spec-poll-rate-limited",
        "mint-async-build",
        "As fetch-spec-async-build, with a bodiless 429 and Retry-After 3 answering the first poll.",
        "A 429 on a poll spends no retry: the client waits the Retry-After seconds, polls "
        "again and keeps the server's pacing after it.",
        _async_fetch(_poll_rate_limited),
    ),
    Synthetic(
        "fetch-spec-poll-refused",
        "mint-async-build",
        "As fetch-spec-async-build, with a 503 service_unavailable answering the first poll, "
        "and two answering the third.",
        "A retryable refused poll other than a 429 spends one of the two retries, and a poll "
        "that answers restores them, so two 503s in a row after an answered poll are polled "
        "again too. A refused poll changes no pacing: the poll after it waits what the "
        "refused one waited.",
        _async_fetch(_poll_busy),
    ),
    Synthetic(
        "fetch-spec-poll-refused-backoff",
        "mint-async-build",
        "As fetch-spec-async-build, with no Retry-After and no retry_after_ms on any "
        "response, and a 503 service_unavailable with no Retry-After answering the second "
        "poll.",
        "With no pacing from the server, each poll waits the next step of the 1, 2, 4, 8 "
        "second backoff, and a refused poll uses a step too: the poll after the 503 waits "
        "4 seconds.",
        _async_fetch(_poll_busy_unpaced),
    ),
    Synthetic(
        "fetch-spec-poll-refused-retry-after",
        "mint-async-build",
        "As fetch-spec-async-build, with a 503 service_unavailable and Retry-After "
        f"{BUSY_POLL_RETRY_AFTER} answering the first poll.",
        "The poll after a refused poll waits the refusal's Retry-After whatever its status, "
        "as a 429's, and the polls after it keep the server's pacing.",
        _async_fetch(_poll_busy_retry_after),
    ),
    Synthetic(
        "fetch-spec-poll-rate-limited-not-retryable",
        "mint-async-build",
        "As fetch-spec-async-build, with a 429 problem document with no code and "
        "retryable false answering the first poll, and the exchanges after it go.",
        "A refusal's retryable outranks its status on a poll too: a 429 that says it is "
        "not retryable raises RateLimitError at once, where a retryable 429 is polled "
        "again without spending a retry.",
        _poll_refused_at_once(429, _RATE_LIMITED_FOR_GOOD),
    ),
    Synthetic(
        "fetch-spec-poll-not-found",
        "mint-async-build",
        "As fetch-spec-async-build, with a 404 operation_not_found answering the first poll, "
        "and the exchanges after it go.",
        "A refused poll that is not retryable raises its error at once, with the one poll "
        "made in long_builds.",
        _poll_refused_at_once(404, _OPERATION_NOT_FOUND),
    ),
    Synthetic(
        "fetch-spec-operation-failed-resubmitted",
        "mint-async-build",
        "As fetch-spec-async-build, with the first poll answering failed with a retryable operation_failed, and the build posted again.",
        "A poll that answers a failed operation whose error is retryable posts the same build "
        "once more and polls the operation it starts; long_builds counts one resubmission.",
        _async_fetch(_operation_failed),
    ),
    Synthetic(
        "fetch-spec-signed-fetch-partial",
        "fetch-spec-known-sentence",
        "The first signed fetch answers 206 with the file's bytes, and the files route is "
        "asked again.",
        "A signed fetch that answers anything but 200 is a failed fetch: the client asks "
        "the files route once more and fetches again, and the second fetch's 200 gives "
        "the dataset.",
        _signed_fetch_partial,
    ),
    Synthetic(
        "fetch-spec-files-route-no-content",
        "fetch-spec-known-sentence",
        "The files route answers 204 with no location, and the exchanges after it go.",
        "A files route that answers a status other than 302, 200 or a refusal raises "
        "ContractError unexpected_response, with that response's trace id, and the "
        "client fetches nothing.",
        _files_route_answers(204, keep_location=False),
    ),
    Synthetic(
        "fetch-spec-files-route-temporary-redirect",
        "fetch-spec-known-sentence",
        "The files route answers 307 with the parent's location, and the exchanges after "
        "it go.",
        "A files route redirects with 302 only: a 307, as any 3xx other than 302, raises "
        "ContractError unexpected_response, and the client does not follow its location.",
        _files_route_answers(307, keep_location=True),
    ),
    Synthetic(
        "fetch-spec-files-route-serves-file",
        "fetch-spec-known-sentence",
        "The files route answers 200 with the file the signed URL served, and the signed "
        "fetch goes.",
        "A files route that answers 200 holds the file as its body, which the client "
        "hashes against the artifact's digest with no further request.",
        _files_route_serves_file,
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
        "fetch-ref-signed-url-refused-twice",
        "fetch-ref",
        "The signed download answers 403, and so does the one the files route names when asked again.",
        "A download asks the files route once and fetches the signed URL once, then asks the "
        "route once more and fetches again, with no wait; a second refused fetch raises "
        "ServerError download_failed.",
        _signed_url_refused_twice,
    ),
    Synthetic(
        "fetch-spec-relative-location",
        "fetch-spec-known-sentence",
        f"The files route's Location becomes the relative URL {RELATIVE_LOCATION!r}, and the "
        "file comes from the path it resolves to.",
        "A relative Location resolves against the files route's URL, not the base URL's "
        "origin, and the client fetches the file there, on the API's origin, reaching the "
        "same dataset.",
        _relative_location,
    ),
    Synthetic(
        "fetch-spec-foreign-artifact-url",
        "fetch-spec-known-sentence",
        f"Every artifact url of the build's manifest becomes absolute on {FOREIGN_ORIGIN}, "
        "and the exchanges after the build go.",
        "An artifact url on an origin other than the API's raises ContractError "
        "contract_violation before any request for the file.",
        _artifact_urls_on(lambda parent: FOREIGN_ORIGIN),
    ),
    Synthetic(
        "fetch-spec-same-origin-artifact-url",
        "fetch-spec-known-sentence",
        "Every artifact url of the build's manifest becomes absolute on the API's origin.",
        "An absolute artifact url on the API's origin names the same files route as its "
        "root-relative form, so the client asks it as that origin_path and reaches the same "
        "dataset.",
        _artifact_urls_on(lambda parent: _origin(parent["origin"]["base_url"])),
    ),
    Synthetic(
        "fetch-spec-storage-artifact-url",
        "fetch-spec-known-sentence",
        f"Every artifact url of the build's manifest becomes absolute on {STORAGE_ORIGIN}, "
        "and the exchanges after the build go.",
        "An artifact url names a files route, which is on the API's origin; one on the "
        "storage host raises ContractError contract_violation before any request for the "
        "file, as on any other origin.",
        _artifact_urls_on(lambda parent: STORAGE_ORIGIN),
    ),
    Synthetic(
        "fetch-spec-manifest-without-identity",
        "fetch-spec-known-sentence",
        "The build's manifest definition loses identity, and the exchanges after it go.",
        "A manifest whose definition has no identity, a strict member, raises ContractError "
        "contract_violation on the build response, before any download.",
        _manifest_without_identity,
    ),
    Synthetic(
        "operation-failed-without-error",
        "operation-resume",
        "The resumed operation's first poll answers it failed with no error member.",
        "A failed operation whose resource carries no error raises ServerError with the code "
        "operation_failed; a resumed operation holds no build request, so nothing is posted "
        "again.",
        _failed_without_error,
    ),
    Synthetic(
        "newest-version-unreadable-page",
        "fetch-ref-newest-version",
        "The versions page names its versions list items.",
        "A versions page with no versions list raises ContractError contract_violation, "
        "since only a NotFoundError reads as no stored version.",
        _versions_renamed,
    ),
    Synthetic(
        "search-sunset-warning",
        "search-place-ambiguous",
        f"The search is made twice, and each response carries Sunset {SUNSET!r} with no "
        "Deprecation header.",
        "A Sunset header alone issues the route_deprecated warning, once per process per "
        "distinct value, so the second search, whose response carries the same value, "
        "warns nothing.",
        _sunset_twice,
    ),
    Synthetic(
        "search-server-warning",
        "search-place-ambiguous",
        "The search is made three times. Its responses carry x-dfo-warning "
        f"{SERVER_MESSAGE!r}, then the same with Sunset {SUNSET!r}, then "
        f"{OTHER_SERVER_MESSAGE!r} with Deprecation {DEPRECATION!r} and the same Sunset.",
        "An x-dfo-warning header issues server_warning once per process per message, and "
        "Deprecation and Sunset issue route_deprecated once per distinct pair of their "
        "values: the second search warns route_deprecated alone, and the third, a new pair "
        "and a new message, warns both.",
        _server_warnings,
    ),
    Synthetic(
        "answer-part-status-not-a-string",
        "answer-mixed-fetch-raises",
        "Part 1's status becomes the number 7, and the fetch goes.",
        "A part whose status is not a string raises ContractError contract_violation at "
        "/parts/1/status, where an unknown string status is an unknown gap.",
        _search_ends(_status_a_number, "contract_violation"),
    ),
    Synthetic(
        "answer-definition-spec-id-mismatch",
        "answer-definite-fetch",
        f"Part 0's spec definition names spec_id {UNKNOWN_SPEC_ID}, and the fetch goes.",
        "A Search spec whose definition.spec_id differs from its spec_id raises ContractError "
        "contract_violation, in a Search response as in a ref.",
        _search_ends(_definition_spec_id_differs, "contract_violation"),
    ),
    Synthetic(
        "answer-spec-without-identity",
        "answer-definite-fetch",
        "Part 0's spec definition loses identity, and the fetch goes.",
        "A Search spec whose definition has no identity, which a client posts back, raises "
        "ContractError contract_violation.",
        _search_ends(_spec_without("identity"), "contract_violation"),
    ),
    Synthetic(
        "answer-spec-without-resolved",
        "answer-definite-fetch",
        "Part 0's spec loses resolved, and the fetch goes.",
        "A Search spec with no resolved, whose entity_groups a client reads, raises "
        "ContractError contract_violation.",
        _search_ends(_spec_without("resolved"), "contract_violation"),
    ),
    Synthetic(
        "answer-nested-specs-unchecked",
        "choose-place-one-part",
        "The no_data part's flagged_match and alternate_match, and the chosen spec's "
        "resolved.equivalent_readings, hold the chosen spec without resolved and without "
        "its definition's identity.",
        "A client checks strict members only where a rule reads them, so a Spec in "
        "no_data.flagged_match, no_data.alternate_match or resolved.equivalent_readings may "
        "lack resolved and identity, and the search and the choice reach the same answers.",
        _nested_specs_unchecked,
    ),
    Synthetic(
        "answer-folded-parts-unnamed",
        "answer-several-readings-folded",
        "The first folded part loses its variable_text and the second's becomes an empty "
        "string.",
        "A definite part whose variable_text is absent or empty adds nothing to its "
        "member's parts, so the member that answers both names no part and its rows' part "
        "value is its sentence.",
        _folded_parts_unnamed,
    ),
    Synthetic(
        "answer-candidate-specs-not-a-list",
        "answer-candidates-choose-fetch",
        "Part 0's candidates.specs becomes an object keyed by index, and the choice and "
        "fetch go.",
        "A strict member of a JSON type its schema does not admit raises ContractError "
        "contract_violation at its pointer, /parts/0/candidates/specs, as a missing one "
        "does.",
        _search_ends(_specs_an_object, "contract_violation"),
    ),
    Synthetic(
        "answer-operations-not-a-list",
        "answer-definite-fetch",
        "Part 0's operations becomes one operation object in place of a list.",
        "A member a client reads that no strict pointer names, and that holds a JSON type "
        "other than its schema's, reads as absent: the part has no operation, and the "
        "fetch issues no SearchWarning and reaches the same dataset.",
        _operations_an_object,
    ),
    Synthetic(
        "answer-empty-parts",
        "search-place-ambiguous",
        "The answer's parts become [].",
        "A resolution with no parts, which the served schema gives at least one, raises "
        "ContractError contract_violation at /parts.",
        _search_ends(_no_parts, "contract_violation"),
    ),
    Synthetic(
        "search-body-not-an-object",
        "search-place-ambiguous",
        "The Search response body becomes a list holding the answer.",
        "A Search response body that is not an object raises ContractError "
        "contract_violation.",
        _search_ends(_not_an_object, "contract_violation"),
    ),
    Synthetic(
        "search-refused-without-code",
        "search-place-ambiguous",
        f"A 503 problem document with no code, retryable false and trace_id {BODY_TRACE_ID} "
        f"answers the search, with x-dfo-trace-id {HEADER_TRACE_ID}.",
        "A problem document with no code raises refusal_without_code with the class its "
        "status maps to, and keeps its body's retryable and trace_id, so a retryable false "
        "503 is not retried and its trace id is the body's, not the header's.",
        _refused_without_code,
    ),
    Synthetic(
        "search-unauthorized",
        "search-place-ambiguous",
        f"A bodiless 401 with x-dfo-trace-id {HEADER_TRACE_ID} answers the search.",
        "A 401 resolves the key again; the key is unchanged, so the client sends nothing "
        "more and raises AuthenticationError refusal_without_code, whose trace id is the "
        "header's, since the refusal has no body.",
        _unauthorized,
    ),
    Synthetic(
        "choose-echoes-separate-and-operations",
        "choose-option-beside-definite",
        f"Part 0, the definite part, carries separate true and the operation {GROWTH}.",
        "A definite part's identity entry echoes the part's separate flag when it is true "
        "and its operations when it has any.",
        _choice_posted(_separate_with_operation),
    ),
    Synthetic(
        "choose-echo-drops-malformed-items",
        "choose-candidate-echoes-assumptions",
        "The definite part's operations hold 42 before an operation and its assumptions "
        "end with a string, and the chosen candidate's reading_assumptions end with 7.",
        "An item that is not an object reads as absent, so each echoed operations and "
        "assumptions list posts only its object items: the definite part's one operation "
        "and its own assumptions, and the chosen candidate's empty list.",
        _echo_drops_malformed,
    ),
    Synthetic(
        "choose-posts-unsupported-placeholder",
        "choose-option-beside-definite",
        "Part 0 becomes an unsupported part with code scope_too_broad and no restatement.",
        "A part that is unsupported and has no restatement posts a placeholder entry with "
        "its unsupported code and variable_text.",
        _choice_posted(_definite_unsupported),
    ),
    Synthetic(
        "choose-refused-unknown-status",
        "choose-option-beside-definite",
        'Part 0 takes the status "deferred" and loses its restatement.',
        "A part of a status the client does not know, with no restatement, cannot be posted "
        "back, so the choice raises InvalidRequestError with no request.",
        _choice_raises("choice_invalid", _definite_deferred),
    ),
    Synthetic(
        "choose-refused-part-without-variable-text",
        "choose-option-beside-definite",
        "Part 0, the definite part, loses its variable_text.",
        "An identity entry carries its part's variable_text, so a part without one cannot "
        "be posted back and the choice raises InvalidRequestError with no request.",
        _choice_raises("choice_invalid", _definite_unnamed),
    ),
    Synthetic(
        "choose-refused-shared-label",
        "choose-place-one-part",
        "The no_data part's second option takes the first one's label, and the search's "
        f"response carries x-dfo-trace-id {ANSWER_TRACE_ID}.",
        "A label two options share matches several, so choosing by it raises "
        "InvalidRequestError with no request. The error describes the caller's choice, so "
        "it carries no trace id although the answer's response has one.",
        _choice_raises(
            "choice_invalid", _labels_shared, {"x-dfo-trace-id": ANSWER_TRACE_ID}
        ),
    ),
    Synthetic(
        "choose-option-two-part-query",
        "choose-place-one-part",
        "The chosen option's data_query holds its one part twice.",
        "A chosen option whose data_query holds other than one part raises ContractError "
        "contract_violation before any request, in a one-part answer as in a several-part "
        "one.",
        _choice_raises("contract_violation", _two_part_query),
    ),
    Synthetic(
        "search-no-json-body",
        "search-place-ambiguous",
        f"The search answers 200 with an empty text/html body and x-dfo-trace-id "
        f"{READ_TRACE_ID}.",
        "A 2xx Search response with no JSON body raises ContractError unexpected_response, "
        "with that response's trace id.",
        _search_ends(_without_json, "unexpected_response"),
    ),
    Synthetic(
        "choose-option-without-data-query",
        "choose-place-one-part",
        "The no_data part's first option loses its data_query.",
        "Choosing an option with no data_query in a one-part answer raises ContractError "
        "contract_violation with no request, as in a several-part answer.",
        _choice_without_data_query,
    ),
    Synthetic(
        "fetch-spec-empty-narrowing",
        "fetch-spec-known-sentence",
        "The fetch names entities {} and facets {}.",
        "An empty entities or facets map narrows nothing, so the build posts no scope, as a "
        "fetch that names neither does.",
        _empty_narrowing,
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
        "An optional member that admits null may be left out or served as null; a client "
        "reads both as "
        "null, and neither changes the outcome. The recording and this record carry the two "
        "forms of the status column's distinct.",
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
        "answer-timed-out",
        "answer-mixed-fetch-raises",
        "The answer's diagnostics.timed_out becomes true, and its response carries "
        f"x-dfo-trace-id {ANSWER_TRACE_ID}.",
        "A no_data part of an answer whose diagnostics say Search timed out is a timed_out gap, "
        "whatever its reason, and fetching the answer raises IncompleteAnswerError, which "
        "describes the answer and carries its response's trace id.",
        _answer_edit(_timed_out, {"x-dfo-trace-id": ANSWER_TRACE_ID}),
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
        "The no_data part's options go, and a choice of option 0 follows the search.",
        "Choosing in a choice gap that offers no options raises InvalidRequestError with no "
        "request.",
        _choice_refused(0, edit=_without_options),
    ),
    Synthetic(
        "choose-candidate-assumed-reading",
        "answer-candidates-choose-fetch",
        "The picked reading's reading_assumptions entry holds one declared default, the pick "
        "echoes it, Search's answer states it, and the fetch goes.",
        "Picking a candidate whose reading_assumptions entry holds an assumption posts that "
        "list unchanged as the identity entry's assumptions. The picked answer's assumptions "
        'and its sentence, closed by the assumption clause ("Assumed: official development '
        "assistance.\"), are Search's response, which the client takes as it stands.",
        _picked_reading_assumes,
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
        'A choice of "Congo [Nowhere]" follows the search.',
        "Choosing an option by a label no option has raises InvalidRequestError with no "
        "request.",
        _choice_refused("Congo [Nowhere]"),
    ),
    Synthetic(
        "choose-candidate-by-label",
        "answer-candidates-choose-fetch",
        "The choice names candidate 0 by its label, and the fetch goes.",
        "A candidate is chosen by its label, the heading of its sentence plan, as by its "
        "index: the choice posts the same identity entry and reaches the same answer.",
        _candidate_by_label,
    ),
    Synthetic(
        "choose-refused-unknown-candidate-label",
        "answer-candidates-choose-fetch",
        'A choice of "Germany\'s net lending" follows the search.',
        "Choosing a candidate by a label no candidate has raises InvalidRequestError "
        "with no request.",
        _choice_refused("Germany's net lending"),
    ),
    Synthetic(
        "choose-refused-shared-candidate-label",
        "answer-candidates-choose-fetch",
        "Candidate 1 takes candidate 0's sentence plan, and the choice names their shared "
        "label.",
        "A label two candidates share matches several, so choosing by it raises "
        "InvalidRequestError with no request.",
        _candidate_label_shared,
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
        'A choice of "Congo [DRC]" on part "Congo" follows the search.',
        'Naming a part by "Congo", a word both parts\' options mention, raises '
        "InvalidRequestError with no request.",
        _choice_refused("Congo [DRC]", "Congo"),
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
    stays. Every body file it removes or writes is checked by `body_path`
    before any is touched, so a bad name or a symlink changes nothing."""
    path = directory / f"{record['name']}.json"
    stale = []
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        for exchange in old["exchanges"]:
            body_file = exchange["response"].get("body_file")
            if body_file and owns_body_file(record["name"], body_file):
                stale.append(body_path(directory, body_file))
    written = {body_path(directory, name): data for name, data in bodies.items()}
    for body in stale:
        body.unlink(missing_ok=True)
    for body, data in written.items():
        body.write_bytes(data)
    _write_json(path, record)


def _refuse_local_host(record: dict[str, Any], base_url: str) -> None:
    """Refuses a record holding an absolute URL other than a redacted signed
    URL on the storage host, or a `location` under `base_url`, as
    `absolute_url_problems` finds them. A local app's host, under any name
    or port, is one no deployed client can fetch."""
    if problems := absolute_url_problems(record, base_url):
        raise SystemExit("\n".join(problems))


def record_recorded(definition: Recorded, ctx: Context, directory: Path) -> None:
    recorded_at = _now()
    session = ctx.session(definition.name)
    definition.run(session, ctx)
    if session.recorder.canned_served:
        raise SystemExit(
            f"{definition.name}: a recorded scenario holds only exchanges the server made, "
            f"and {session.recorder.canned_served} were canned"
        )
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
    _refuse_local_host(record, ctx.base_url)
    write_record(directory, record, session.recorder.bodies)


def record_synthetic(definition: Synthetic, ctx: Context, directory: Path) -> None:
    parent_path = directory / f"{definition.derived_from}.json"
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    derived = definition.derive(parent, replace(ctx, transport=_offline))
    origin = {
        "kind": "synthetic",
        "derived_from": definition.derived_from,
        "edit": definition.edit,
    }
    record = _record(definition.name, definition.description, origin, derived)
    _refuse_local_host(record, parent["origin"]["base_url"])
    write_record(directory, record, derived.bodies)


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
            data = body_path(self.body_dir, response["body_file"]).read_bytes()
        else:
            data = b""
        return Response(response["status"], dict(response.get("headers", {})), data)


def _offline(method: str, url: str, **_: Any) -> Response:
    raise SystemExit(f"this derivation makes no request, and it sent {method} {url}")


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
                    _step_spec(spec),
                    window=tuple(window) if isinstance(window, list) else window,
                    entities=step.get("entities"),
                    facets=step.get("facets"),
                    layout=step.get("layout"),
                    strict=step.get("strict", False),
                )
            case {"op": "fetch", "ref": ref}:
                session.fetch_ref(ref)
            case {"op": "newest_version", "spec": spec}:
                session.newest_version(_step_spec(spec))
            case {"op": "fetch"}:
                if answer is None:
                    raise ValueError("a fetch of an answer follows no answer")
                session.fetch_answer(
                    answer,
                    partial=step.get("partial", False),
                    strict=step.get("strict", False),
                    layout=step.get("layout"),
                )
            case {"op": "mint", "spec": spec}:
                session.mint(
                    _step_spec(spec),
                    layout=step.get("layout"),
                )
            case {"op": "operation", "operation_id": operation_id}:
                session.resume(operation_id, layout=step.get("layout"))
            case {"op": "result"}:
                session.result()


def rederive_recorded(name: str, ctx: Context, directory: Path) -> None:
    """Derives a recorded scenario's `expect` again from its own steps over
    its own exchanges, with no request, and keeps the rest of the record."""
    path = directory / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{name}: no record to derive from; record it live")
    record = json.loads(path.read_text(encoding="utf-8"))
    for exchange in record["exchanges"]:
        if "body_file" in exchange["response"]:
            try:
                body_path(directory, exchange["response"]["body_file"])
            except ValueError as exc:
                raise SystemExit(f"{name}: {exc}") from exc
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
