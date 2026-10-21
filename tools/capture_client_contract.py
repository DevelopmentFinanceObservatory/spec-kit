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
`X-Goog-Signature=redacted`. A scenario that needs a route or status
`served/openapi.json` does not declare, or whose parent does, waits: the
run names it and writes nothing for it, unless `--scenarios-dir` writes
outside the package. The run ends by validating the whole contract with
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from validate_client_contract import (
    FILE_EXPIRED,
    FILE_GONE,
    NO_VERSIONS,
    REBUILD_REFUSED,
    UNNAMED_VARIABLE_TEXT,
    VERSION_UNAVAILABLE,
    Answer,
    ClientError,
    LongBuild,
    answer_from_body,
    answer_gaps,
    answer_members,
    answer_outcome,
    candidate_answer,
    choice_for,
    choice_input,
    data_warning,
    dataset_summary,
    digest_of,
    gap_kinds,
    owns_body_file,
    part_label,
    pick_artifact,
    posted_scope,
    refusal_error,
    rendering_for,
    resubmitted_sentence,
    search_warning_code,
    search_warns,
    selector_path,
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
    """The result a step produced; a step that refused cannot be acted on."""
    if not results:
        raise ShapeError(f"{step} refused, so the steps after it cannot run")
    return results[0]


class ShapeError(Exception):
    """The live answer no longer has the shape the scenario is named for."""


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

    @property
    def gap_kinds(self) -> dict[str, str]:
        return gap_kinds(self.actions)

    def _step(self, step: dict[str, Any], run: Callable[[], dict[str, Any]]) -> None:
        self.call.append(step)
        self.long_builds = []
        try:
            outcome = run()
        except ClientError as error:
            outcome = error.outcome(self.client_codes)
        if self.long_builds:
            (value,) = outcome.values()
            value["long_builds"] = self.long_builds
        self.expect.append(outcome)

    def _answered(self, answer: Answer) -> dict[str, Any]:
        return answer_outcome(answer, self.gap_kinds)

    def _resolve(self, input_: dict[str, Any]) -> Answer:
        response = self.recorder.api(
            "POST", "/search/resolve", {"schema_version": 1, "input": input_}
        )
        return answer_from_body(self._acted(response))

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
                raise ClientError("answer_declined")
            gaps = answer_gaps(answer, self.gap_kinds)
            readings = _members(answer)
            if gaps and (not partial or not readings):
                raise ClientError("answer_incomplete")
            warns = search_warns(answer)
            if warns and strict:
                raise ClientError(search_warning_code(answer), error="SearchWarning")
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

    def mint(self, spec: Spec, *, layout: str | None = None) -> str:
        operation: list[str] = []

        def run() -> dict[str, Any]:
            rendering = rendering_for(layout)
            body = _build_body(spec, None, rendering)
            response = self.recorder.api("POST", "/datasets", body)
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
            response = self.recorder.api("GET", path)
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
        response = self.recorder.api("POST", "/datasets", body)
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
        definition = manifest["definition"]
        sentence = None if spec is None else spec.sentence
        if spec is None or definition["spec_id"] != spec.spec_id or sentence is None:
            sentence = self._identity_sentence(parts, definition)
        digest = artifact["artifact_digest"]
        rows, columns = self.read_shape(self._download(artifact["url"], digest))
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

    def _identity_sentence(self, parts: list[str], definition: dict[str, Any]) -> str:
        entry = {
            "entry_kind": "identity",
            "variable_text": parts[0] if parts else UNNAMED_VARIABLE_TEXT,
            "identity": definition["identity"],
        }
        answer = self._resolve({"kind": "dataset_resubmit", "entries": [entry]})
        return resubmitted_sentence(answer, definition["spec_id"])

    def _download(self, target: str, digest: str, *, origin: bool = True) -> bytes:
        """A file through the route that redirects to it: an artifact's
        root-relative url, or a path under the base URL. A refused signed URL
        or bytes of another digest ask the route once more."""
        failure = ClientError("download_failed")
        for _ in range(2):
            files = (
                self.recorder.origin("GET", target)
                if origin
                else self.recorder.api("GET", target)
            )
            self._raise_problem(files)
            require(files.status == 302, f"the files route answered {files.status}")
            data = self.recorder.external(files.headers["location"])
            if data.status >= 400:
                failure = ClientError("download_failed")
                continue
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
            if build.polled(response.status, response.headers, _json(response)):
                again = self.recorder.api("POST", "/datasets", request)
                build.reposted(again.status, again.headers, _json(again))
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
            manifest = self._acted(
                self.recorder.api("GET", f"/datasets/{spec_id}/{version}")
            )
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
            if manifest["content_version"] == version:
                return self._member(member["parts"], held, manifest, rendering, False)
            newest = manifest["content_version"]
        raise ClientError(
            refusal.code if refusal is not None else "version_unavailable",
            error=VERSION_UNAVAILABLE,
            newest_version=newest,
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
                return self._download(artifact["url"], digest), None
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
            body = self._acted(
                self.recorder.api("GET", f"/datasets/{spec_id}/versions")
            )
        except ClientError as error:
            if error.error_class(self.client_codes) in NO_VERSIONS:
                return None
            raise
        versions = body.get("versions") if isinstance(body, dict) else None
        first = versions[0] if isinstance(versions, list) and versions else None
        newest = first.get("content_version") if isinstance(first, dict) else None
        return newest if isinstance(newest, str) else None

    def _acted(self, response: Response) -> Any:
        self._raise_problem(response)
        return response.json()

    def _raise_problem(self, response: Response) -> None:
        if response.status < 400:
            return
        raise refusal_error(response.status, _json(response), self.actions)


def _json(response: Response) -> Any:
    """A response's JSON body, or None for an empty one."""
    return response.json() if response.body else None


def _operation_outcome(resource: dict[str, Any]) -> dict[str, Any]:
    operation = resource["operation"]
    return {"operation": {k: operation[k] for k in ("operation_id", "status")}}


def _build_body(
    spec: Spec, scope: dict[str, Any] | None, rendering: Mapping[str, Any]
) -> dict[str, Any]:
    input_: dict[str, Any] = {
        "kind": "spec_id",
        "spec_id": spec.spec_id,
        "definition": spec.definition,
    }
    if scope:
        input_["scope"] = scope
    return {"schema_version": 1, "input": input_, "options": dict(rendering)}


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
            member = session.build(parts, spec, None, dict(RENDERING))
            self._cache["ref"] = {"format_version": 1, "members": [member.ref()]}
        return self._cache["ref"]

    def async_spec(self) -> Spec:
        """The spec whose estimate is just above the sync budget."""
        (part,) = self.definite_answer(ASYNC).parts
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
                self.async_spec()
            )
        return self._cache["operation"]


# A route and status the served document must declare before a scenario that
# needs them is recorded: the method, the route template as the served
# document names it, and the status.
type Need = tuple[str, str, str]
ASYNC_BUILD: Need = ("POST", "/api/datasets", "202")
FILE_SELECTOR: Need = ("GET", "/api/datasets/{id}/{content_version}/file", "302")


def unmet(needs: Sequence[Need], openapi: Mapping[str, Any]) -> list[Need]:
    """The needs the served document does not declare."""
    return [
        (method, path, status)
        for method, path, status in needs
        if status
        not in openapi["paths"]
        .get(path, {})
        .get(method.lower(), {})
        .get("responses", {})
    ]


@dataclass(frozen=True)
class Recorded:
    name: str
    description: str
    run: Callable[[Session, Context], None]
    needs: tuple[Need, ...] = ()


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
    s.mint(ctx.async_spec())
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
        needs=(ASYNC_BUILD,),
    ),
    Recorded(
        "operation-resume",
        "Resuming a long build another process started reads its operation by id, and the "
        "operation is pending or running. Waiting polls it at the server's pacing until done; "
        "the client holds no spec, so it takes the sentence from Search by posting the built "
        'identity with variable_text "dataset", then downloads the file. A resumed build holds '
        "no request, so a failed operation is not posted again.",
        _operation_resume,
        needs=(ASYNC_BUILD,),
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
    bodies: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class Synthetic:
    """A scenario derived from a recorded one. A `live` derivation records
    some of its exchanges against the base URL. A synthetic scenario waits
    for its own needs and its parent's."""

    name: str
    derived_from: str
    edit: str
    description: str
    derive: Callable[[dict[str, Any], Context], Derived]
    live: bool = False
    needs: tuple[Need, ...] = ()


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
_OPERATION_FAILED = _problem(
    "operation_failed",
    500,
    "Internal Server Error",
    "The build's worker stopped before it finished.",
    retryable=True,
)
# The content version a rebuild answers when the data moved on.
NEWER_VERSION = "cv_" + "1" * 32


def _retry_after_504(parent: dict[str, Any], ctx: Context) -> Derived:
    exchanges = copy.deepcopy(parent["exchanges"])
    index = _build_index(parent)
    exchanges.insert(index, _refusal(exchanges[index]["request"], 504, _TIMEOUT))
    return _same(parent, exchanges)


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
    return [refused, copy.deepcopy(refused)], error


def _not_resolvable(
    build: dict[str, Any], ctx: Context
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    error = refusal_error(422, _NOT_RESOLVABLE, ctx.actions).outcome(ctx.client_codes)
    return [_refusal(build["request"], 422, _NOT_RESOLVABLE)], error


def _strict_warning(parent: dict[str, Any], ctx: Context) -> Derived:
    index = _build_index(parent)
    manifest = parent["exchanges"][index]["response"]["json"]
    warning = data_warning(manifest)
    require(warning is not None, "the manifest carries a warning above info")
    call = copy.deepcopy(parent["call"])
    call[-1]["strict"] = True
    return Derived(
        call,
        copy.deepcopy(parent["exchanges"][: index + 1]),
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
    """Search answers the built identity with the spec the fetch posted
    rather than the one the build answered."""
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
    return Derived(
        copy.deepcopy(parent["call"]),
        exchanges,
        [*copy.deepcopy(parent["expect"][:-1]), ctx.raised("contract_violation")],
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
        FILE_EXPIRED, error=VERSION_UNAVAILABLE, newest_version=NEWER_VERSION
    )
    return Derived(
        [{"op": "fetch", "ref": ref}],
        [_snapshot_expired(ref), build],
        [error.outcome(ctx.client_codes)],
    )


def _expired_not_reproducible(parent: dict[str, Any], ctx: Context) -> Derived:
    """The parent's ref, its snapshot expired and its rebuild refused; the
    versions route is recorded live."""
    ref = copy.deepcopy(parent["call"][0]["ref"])
    (member,) = ref["members"]
    spec = Spec(member["spec"]["spec_id"], member["spec"]["definition"])
    body = _build_body(spec, None, member["artifact"]["rendering"])
    canned = [
        _snapshot_expired(ref),
        _refusal(
            {"method": "POST", "path": "/datasets", "json": body}, 422, _NOT_RESOLVABLE
        ),
    ]
    session = ctx.session("synthetic", canned=canned)
    session.fetch_ref(ref)
    require(
        session.expect[-1].get("error", {}).get("class") == VERSION_UNAVAILABLE,
        "the lookup raises VersionUnavailableError",
    )
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
    same exchanges; `change` edits the exchanges and the fetch's outcome."""

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
        "fetch-spec-504-twice",
        "fetch-spec-known-sentence",
        "A 504 timeout with retryable true answers the build and its one retry.",
        "A synchronous build cut off twice is retried once only: the second 504 raises the "
        "timeout code's ServerError.",
        _ends_at_build(_cut_off_twice),
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
        "fetch-spec-strict-warning",
        "fetch-spec-known-sentence",
        "The fetch is strict, and the exchanges after the build go.",
        "A strict fetch raises the manifest's first warning above info severity, "
        "coverage_below_scope at warn here, as DataWarning before the download.",
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
        "fetch-ref-expired-not-reproducible",
        "fetch-ref",
        "The snapshot refuses 410 artifact_expired and the rebuild 422 member_not_resolvable; the versions route is recorded live.",
        "When the stored version has expired and the build is refused, the client reads the "
        "versions route for the newest version. Dev refuses the keyless recorder 403 "
        "publish_not_permitted there, so the client raises VersionUnavailableError with the "
        "snapshot's artifact_expired code and no newest version.",
        _expired_not_reproducible,
        live=True,
    ),
    Synthetic(
        "fetch-ref-expired-file-selector",
        "fetch-ref",
        "The files route refuses 410 artifact_expired, and the file selector redirects to the parent's signed download.",
        "When the files route refuses artifact_expired, the client asks the file selector for "
        "the stored version in the ref's rendering and downloads the file it redirects to, "
        "which hashes to the stored digest.",
        _expired_file_selector,
        needs=(FILE_SELECTOR,),
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
        "fetch-spec-operation-failed-resubmitted",
        "mint-async-build",
        "As fetch-spec-async-build, with the first poll answering failed with a retryable operation_failed, and the build posted again.",
        "A poll that answers a failed operation whose error is retryable posts the same build "
        "once more and polls the operation it starts; long_builds counts one resubmission.",
        _async_fetch(_operation_failed),
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
        derived.bodies,
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
                    entities=step.get("entities"),
                    facets=step.get("facets"),
                    layout=step.get("layout"),
                    strict=step.get("strict", False),
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
                    layout=step.get("layout"),
                )
            case {"op": "mint", "spec": spec}:
                session.mint(
                    Spec(spec["spec_id"], spec["definition"], spec.get("sentence")),
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


def waiting(openapi: Mapping[str, Any]) -> dict[str, list[str]]:
    """Each scenario that waits for a route or status `openapi` does not
    declare, its own or its parent's, with what it waits for."""
    recorded = {d.name: d.needs for d in RECORDED}
    found = {}
    for definition in (*RECORDED, *SYNTHETIC):
        needs = definition.needs
        if isinstance(definition, Synthetic):
            needs = (*needs, *recorded.get(definition.derived_from, ()))
        if missing := unmet(needs, openapi):
            found[definition.name] = [" ".join(need) for need in missing]
    return found


def run_record(args: argparse.Namespace) -> int:
    package_dir: Path = args.package_dir
    directory: Path = args.scenarios_dir or package_dir / "scenarios"
    names = {d.name for d in (*RECORDED, *SYNTHETIC)}
    unknown = set(args.only or ()) - names
    if unknown:
        raise SystemExit(f"no scenario is named {sorted(unknown)}")
    served = json.loads(
        (package_dir / "served" / "openapi.json").read_text(encoding="utf-8")
    )
    chosen = set(args.only or names)
    # A record outside the package is not validated, so it waits for nothing.
    for name, needs in waiting(served).items() if args.scenarios_dir is None else ():
        if name in chosen:
            print(
                f"waiting {name}: the served document declares no {needs}", flush=True
            )
            chosen.discard(name)
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
