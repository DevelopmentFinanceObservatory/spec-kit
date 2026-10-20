# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
# ]
# ///
"""Validate the client contract under
`python/client-contract/src/dfo_client_contract/`.

Usage:
    uv run tools/validate_client_contract.py

Checks that every packaged schema is valid draft 2020-12 and that every
document validates against its schema, then what the schemas cannot express:

- `served/SOURCE.json` names a 40-character dfo-apis commit.
- Every `strict-fields.json` pointer resolves in `served/openapi.json` and
  names each property once. A required entry's property stays in its schema's
  `required` list and admits no null; an enum entry's property admits every
  value listed.
- `actions.json` lists codes in order, each once. Every active refusal code in
  `registries/codes.json` with a `problem_details` or `no_data_reason` mapping
  has a row, and no row names another code. A row carries `gap_kind` exactly
  when its code has a `no_data_reason` mapping; `choose` goes with the
  `choice` gap kind and only with it; and a code with a `problem_details`
  mapping takes an action a problem document can carry.
- `client-codes.json` lists codes in order, each once, none of them a code in
  `actions.json` or `registries/codes.json`. Each row's class is one a
  scenario's error may name, other than a warning's, and only
  `refusal_without_code` takes the class its status maps to.
- Each scenario's file name matches its `name`, names are unique, `expect`
  holds one outcome per step, a step that acts on an earlier result follows
  one, a synthetic scenario derives from a recorded one, and every body file
  exists and is the scenario's own or, for a synthetic scenario, one its
  parent names. In a recorded scenario each API request matches a route and
  method in `served/openapi.json`, its JSON body validates against the
  route's request schema, each JSON response body validates against the
  route's schema for its status, and a problem document's `code` is an active
  code registered with that status.
- Each error a scenario expects, other than a `SearchWarning` or
  `DataWarning`, carries a refusal's code from its exchanges, a failed
  operation's included, or a client code of its class.
- Each scenario's `expect` equals the outcomes its steps reach over its
  exchanges, with refusals' error classes and gap kinds from `actions.json`
  and the class of each error the client raises itself from
  `client-codes.json`, as `expected_outcomes` derives them, the waits
  before each poll included; a dataset's rows, columns and per-part row
  counts are left to the Parquet bodies, and each download hashes to its
  artifact's digest or the outcome says it does not. Each Search request a
  record holds posts the input the steps derive: the question, a choice's
  data_query or dataset_resubmit entries, or a member's identity, and each
  build posts the options its layout gives. Every
  absolute URL a record holds, a `url` request or a `location` header, has
  the query `X-Goog-Signature=redacted`.
- Each ref example's file name matches its `name` and gets the verdict it
  states: from `dataset-ref.schema.json`, then from the rules that each
  member's definition carries its own `spec_id` and that members hold
  distinct `spec_id`s.
- Each rule file under `rules/` validates against `rules.schema.json`, names
  the rule its file name names, and names each vector once.

A recorded body is checked with one allowance: a required member whose schema
admits null may be absent, because the server leaves null members out of the
bodies it serves. It does not import `dfo_client_contract`, whose tests replay
the rule vectors. `tools/capture_client_contract.py` imports the outcome
rules from here, so the records are written by the code that checks them.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "python" / "client-contract" / "src" / "dfo_client_contract"
REGISTRY_PATH = REPO_ROOT / "registries" / "codes.json"

_OPENAPI_URI = "urn:dfo-client-contract:served-openapi"
_ACTION_CHANNELS = {"problem_details", "no_data_reason"}
_ANSWER_ONLY_ACTIONS = {"choose", "ask_data_question"}
# The steps each step that acts on an earlier result may follow. A fetch of the
# answer acts on the last search or choice, so it may follow another such fetch.
_ACTS_ON_PREVIOUS = {
    "choose": {"search", "choose"},
    "fetch_answer": {"search", "choose", "fetch_answer"},
    "result": {"mint", "operation"},
}
_STRICT_POINTER = re.compile(r"^/components/schemas/([^/]+)/properties/([^/]+)$")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
    )


def _where(error: jsonschema.ValidationError) -> str:
    return "/".join(str(part) for part in error.absolute_path) or "<root>"


def _schema_problems(
    label: str, validator: jsonschema.Draft202012Validator, document: Any
) -> list[str]:
    return [
        f"{label}: schema: {_where(error)}: {error.message}"
        for error in sorted(
            validator.iter_errors(document), key=lambda e: list(e.absolute_path)
        )
    ]


def _contract_validators() -> dict[str, jsonschema.Draft202012Validator]:
    """One validator per packaged schema, sharing a registry so the scenario
    schema's `$ref` to `dataset-ref.schema.json` resolves by `$id`."""
    names = (
        "strict-fields.schema.json",
        "actions.schema.json",
        "client-codes.schema.json",
        "scenario.schema.json",
        "dataset-ref.schema.json",
        "rules/rules.schema.json",
    )
    schemas = {name: _load(PACKAGE_DIR / name) for name in names}
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return {
        name: jsonschema.Draft202012Validator(
            schema,
            registry=registry,
            format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
        )
        for name, schema in schemas.items()
    }


# --- the served document ----------------------------------------------------


def _resolve(document: Any, pointer: str) -> Any:
    """The value at an RFC 6901 `pointer` in `document`; raises `KeyError`."""
    node = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                raise KeyError(pointer)
            node = node[int(token)]
        elif isinstance(node, dict) and token in node:
            node = node[token]
        else:
            raise KeyError(pointer)
    return node


def _expand(openapi: dict[str, Any], schema: Any) -> Iterator[dict[str, Any]]:
    """`schema` and every schema its `$ref`, `anyOf`, `oneOf` and `allOf`
    reach, local references followed through `openapi`."""
    if not isinstance(schema, dict):
        return
    yield schema
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        yield from _expand(openapi, _resolve(openapi, ref[1:]))
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, []):
            yield from _expand(openapi, branch)


def _openapi_registry(openapi: dict[str, Any]) -> Registry:
    return Registry().with_resource(_OPENAPI_URI, DRAFT202012.create_resource(openapi))


def _admits_null(registry: Registry, pointer: str) -> bool:
    """Whether the schema at `pointer` accepts null. A schema with no type,
    or a union with an open branch, accepts null as well as one that names
    it."""
    schema = {"$ref": f"{_OPENAPI_URI}#{pointer}"}
    return jsonschema.Draft202012Validator(schema, registry=registry).is_valid(None)


def _allowed_values(openapi: dict[str, Any], schema: Any) -> set[str]:
    values: set[str] = set()
    for branch in _expand(openapi, schema):
        if "const" in branch:
            values.add(branch["const"])
        values.update(branch.get("enum", []))
    return values


def _check_source(source: Any) -> list[str]:
    if not isinstance(source, dict):
        return ["served/SOURCE.json: not an object"]
    problems = []
    if source.get("format_version") != 1:
        problems.append("served/SOURCE.json: format_version is not 1")
    if not isinstance(source.get("repository"), str):
        problems.append("served/SOURCE.json: repository is not a string")
    if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit"))):
        problems.append("served/SOURCE.json: commit is not a 40-character sha")
    if not isinstance(source.get("captured_from"), str):
        problems.append("served/SOURCE.json: captured_from is not a string")
    return problems


def _check_strict_fields(
    document: dict[str, Any], openapi: dict[str, Any]
) -> list[str]:
    problems = []
    registry = _openapi_registry(openapi)
    for kind in ("required", "enums"):
        pointers = [entry["pointer"] for entry in document[kind]]
        problems.extend(
            f"strict-fields.json: {kind}: {pointer} is listed twice"
            for pointer in sorted({p for p in pointers if pointers.count(p) > 1})
        )
        for entry in document[kind]:
            pointer = entry["pointer"]
            try:
                prop = _resolve(openapi, pointer)
            except KeyError:
                problems.append(
                    f"strict-fields.json: {pointer} does not resolve in "
                    "served/openapi.json"
                )
                continue
            if kind == "enums":
                missing = [
                    v
                    for v in entry["values"]
                    if v not in _allowed_values(openapi, prop)
                ]
                if missing:
                    problems.append(
                        f"strict-fields.json: {pointer} no longer admits {missing}"
                    )
                continue
            match = _STRICT_POINTER.match(pointer)
            if match is None:  # the strict-fields schema pins the pointer form
                continue
            schema_name, field = match.groups()
            required = openapi["components"]["schemas"][schema_name].get("required", [])
            if field not in required:
                problems.append(
                    f"strict-fields.json: {pointer} is not required in {schema_name}"
                )
            if _admits_null(registry, pointer):
                problems.append(f"strict-fields.json: {pointer} admits null")
    return problems


# --- actions ----------------------------------------------------------------


def _check_actions(document: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    rows = document["codes"]
    codes = [row["code"] for row in rows]
    problems = [
        f"actions.json: code {code!r} is listed twice"
        for code in sorted({c for c in codes if codes.count(c) > 1})
    ]
    if codes != sorted(codes):
        problems.append("actions.json: codes are not sorted")

    entries = {entry["code"]: entry for entry in registry["entries"]}
    channels = {
        code: {mapping["channel"] for mapping in entry["mappings"]}
        for code, entry in entries.items()
    }
    expected = {
        code
        for code, entry in entries.items()
        if entry["lifecycle"] == "active"
        and entry["classification"] == "refusal"
        and channels[code] & _ACTION_CHANNELS
    }
    problems.extend(
        f"actions.json: active code {code!r} has no row"
        for code in sorted(expected - set(codes))
    )
    for row in rows:
        code = row["code"]
        entry = entries.get(code)
        if entry is None:
            problems.append(f"actions.json: {code!r} is not in the registry")
            continue
        if code not in expected:
            problems.append(
                f"actions.json: {code!r} is {entry['lifecycle']} "
                f"{entry['classification']} on {sorted(channels[code])}, outside "
                "the active refusals on problem_details and no_data_reason"
            )
            continue
        on_no_data = "no_data_reason" in channels[code]
        if on_no_data != ("gap_kind" in row):
            problems.append(
                f"actions.json: {code!r} must carry gap_kind exactly when it maps "
                "to no_data_reason"
            )
        if (row["action"] == "choose") != (row.get("gap_kind") == "choice"):
            problems.append(
                f"actions.json: {code!r} pairs action {row['action']!r} with "
                f"gap_kind {row.get('gap_kind')!r}; choose goes with choice only"
            )
        if "problem_details" in channels[code] and row["action"] in (
            _ANSWER_ONLY_ACTIONS
        ):
            problems.append(
                f"actions.json: {code!r} reaches problem documents, which cannot "
                f"carry action {row['action']!r}"
            )
    return problems


# --- client codes -----------------------------------------------------------


def error_classes(scenario_schema: dict[str, Any]) -> list[str]:
    """The error classes a scenario's error outcome may name."""
    return scenario_schema["$defs"]["error"]["properties"]["class"]["enum"]


def _check_client_codes(
    document: dict[str, Any],
    actions: dict[str, Any],
    registry: dict[str, Any],
    classes: Sequence[str],
) -> list[str]:
    codes = [row["code"] for row in document["codes"]]
    problems = [
        f"client-codes.json: code {code!r} is listed twice"
        for code in sorted({c for c in codes if codes.count(c) > 1})
    ]
    if codes != sorted(codes):
        problems.append("client-codes.json: codes are not sorted")
    taken = {
        "actions.json": {row["code"] for row in actions["codes"]},
        "registries/codes.json": {entry["code"] for entry in registry["entries"]},
    }
    for source, server_codes in taken.items():
        problems.extend(
            f"client-codes.json: {code!r} is also a code in {source}"
            for code in sorted(set(codes) & server_codes)
        )
    # A warning raised under strict carries the server's code.
    known = {*classes, BY_STATUS} - set(WARNING_CLASSES)
    problems.extend(
        f"client-codes.json: {row['code']!r} raises {row['class']!r}, a class "
        "no scenario names"
        for row in document["codes"]
        if row["class"] not in known
    )
    by_status = [row["code"] for row in document["codes"] if row["class"] == BY_STATUS]
    if by_status != [REFUSAL_WITHOUT_CODE]:
        problems.append(
            f"client-codes.json: {by_status} take the class {BY_STATUS}, where "
            f"{REFUSAL_WITHOUT_CODE!r} alone does"
        )
    return problems


# --- scenarios --------------------------------------------------------------


class _Routes:
    """The served document's routes, matched against concrete paths."""

    def __init__(self, openapi: dict[str, Any]) -> None:
        self.openapi = openapi
        self._templates = [(t, t.split("/")) for t in openapi["paths"]]
        registry = _openapi_registry(openapi)
        self._validator = jsonschema.validators.extend(
            jsonschema.Draft202012Validator, {"required": _required_unless_nullable}
        )
        self._registry = registry

    def template(self, path: str) -> str | None:
        """The route template matching `path`, preferring a literal segment
        over a parameter, as the server's router does."""
        segments = path.split("/")
        matches = [
            template
            for template, parts in self._templates
            if len(parts) == len(segments)
            and all(
                part == segment or (part.startswith("{") and bool(segment))
                for part, segment in zip(parts, segments, strict=True)
            )
        ]
        return min(matches, key=lambda t: t.count("{"), default=None)

    def operation(
        self, base_path: str, request: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any] | None] | None:
        """The route template an API request matches, its method's JSON
        pointer under `/paths`, and the operation it serves; None for a
        request outside the API or one that matches no route."""
        if "url" in request:
            return None
        template = self.template(_api_path(base_path, request))
        if template is None:
            return None
        method = request["method"].lower()
        route = f"/paths/{_pointer_token(template)}/{method}"
        return template, route, self.openapi["paths"][template].get(method)

    def response_schema(
        self, base_path: str, request: dict[str, Any], status: int
    ) -> dict[str, Any] | None:
        """The JSON body schema `request`'s route declares for `status`."""
        found = self.operation(base_path, request)
        if found is None or found[2] is None:
            return None
        responses = found[2].get("responses", {})
        declared = str(status) if str(status) in responses else "default"
        content = responses.get(declared, {}).get("content", {})
        media = _body_schema(content)
        return None if media is None else content[media].get("schema")

    def validate(self, pointer: str, body: Any) -> list[str]:
        validator = self._validator(
            {"$ref": f"{_OPENAPI_URI}#{pointer}"}, registry=self._registry
        )
        return [
            f"{_where(error)}: {error.message[:300]}"
            for error in validator.iter_errors(body)
        ]


def _required_unless_nullable(
    validator: Any, required: list[str], instance: Any, schema: dict[str, Any]
) -> Iterator[jsonschema.ValidationError]:
    """`required`, except that a member whose schema admits null may be
    absent: the server leaves null members out of the bodies it serves."""
    if not validator.is_type(instance, "object"):
        return
    properties = schema.get("properties", {})
    for name in required:
        if name in instance:
            continue
        member = properties.get(name)
        if member is not None and validator.evolve(schema=member).is_valid(None):
            continue
        yield jsonschema.ValidationError(f"{name!r} is a required property")


def _api_path(base_path: str, request: dict[str, Any]) -> str:
    """The path an API request reaches: a `path` under the base URL's path,
    or an `origin_path` as it stands."""
    raw = base_path + request["path"] if "path" in request else request["origin_path"]
    return urlsplit(raw).path


def _pointer_token(text: str) -> str:
    return text.replace("~", "~0").replace("/", "~1")


def _body_schema(content: dict[str, Any]) -> str | None:
    for media in ("application/json", "application/problem+json"):
        if media in content:
            return media
    return None


def _check_exchange(
    label: str,
    exchange: dict[str, Any],
    base_path: str,
    routes: _Routes,
    registry: dict[str, Any],
) -> list[str]:
    request, response = exchange["request"], exchange["response"]
    if "url" in request:
        return []
    found = routes.operation(base_path, request)
    if found is None:
        path = _api_path(base_path, request)
        return [f"{label}: {path} matches no route in served/openapi.json"]
    template, route, operation = found
    if operation is None:
        return [f"{label}: {template} serves no {request['method']}"]

    problems = []
    if "json" in request:
        media = _body_schema(operation.get("requestBody", {}).get("content", {}))
        if media is None:
            problems.append(f"{label}: {template} takes no JSON request body")
        else:
            pointer = f"{route}/requestBody/content/{_pointer_token(media)}/schema"
            problems.extend(
                f"{label}: request body: {problem}"
                for problem in routes.validate(pointer, request["json"])
            )

    responses = operation.get("responses", {})
    status = str(response["status"])
    declared = status if status in responses else "default"
    if declared not in responses:
        return [*problems, f"{label}: {template} declares no {status} response"]
    if "json" in response:
        media = _body_schema(responses[declared].get("content", {}))
        if media is None:
            problems.append(f"{label}: {template} {status} has no JSON body")
        else:
            pointer = (
                f"{route}/responses/{declared}/content/{_pointer_token(media)}/schema"
            )
            problems.extend(
                f"{label}: response body: {problem}"
                for problem in routes.validate(pointer, response["json"])
            )
        problems.extend(_check_problem_code(label, response, registry))
    return problems


def _check_problem_code(
    label: str, response: dict[str, Any], registry: dict[str, Any]
) -> list[str]:
    body = response["json"]
    if response["status"] < 400 or not isinstance(body, dict) or "code" not in body:
        return []
    for entry in registry["entries"]:
        if entry["code"] != body["code"]:
            continue
        statuses = {
            m.get("http_status")
            for m in entry["mappings"]
            if m["channel"] == "problem_details"
        }
        if entry["lifecycle"] != "active" or response["status"] not in statuses:
            return [
                (
                    f"{label}: problem code {body['code']!r} is not an active code "
                    f"registered with status {response['status']}"
                )
            ]
        return []
    return [f"{label}: problem code {body['code']!r} is not in the registry"]


# --- outcomes ---------------------------------------------------------------
#
# A client's outcome for each step follows from the exchanges and from
# `actions.json`. The functions here derive it; the validator checks each
# record's `expect` against them, and `tools/capture_client_contract.py`
# imports them to write `expect` when it records.

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
# The class of a refusal whose code has no row, by HTTP status. Any other 5xx
# is a ServerError and any other 4xx an InvalidRequestError.
STATUS_CLASSES = {
    400: "InvalidRequestError",
    401: "AuthenticationError",
    403: "PermissionDeniedError",
    404: "NotFoundError",
    408: "DfoTimeoutError",
    410: "NotFoundError",
    422: "InvalidRequestError",
    429: "RateLimitError",
}
# The statuses a refusal is retried on when its body does not say.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
# The website proxy's `retry` value that makes its refusal retryable.
_PROXY_RETRY = "retry_after_delay"
# The retries a client makes by default after a retryable refusal.
MAX_RETRIES = 2


def gap_kinds(actions: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """The gap kind of each no_data reason in actions.json, keyed by code."""
    return {code: row["gap_kind"] for code, row in actions.items() if "gap_kind" in row}


def refusal_code(body: Any) -> str | None:
    """A refusal's code: a problem document's `code`, or the `error` member
    of the website proxy's own `{error, retry, detail}` body."""
    if not isinstance(body, dict):
        return None
    code = body.get("code", body.get("error"))
    return code if isinstance(code, str) else None


def error_class(
    status: int, code: str | None, actions: Mapping[str, Mapping[str, Any]]
) -> str:
    """The error class a client raises for a refusal: its code's action, else
    its HTTP status."""
    row = actions.get(code) if code is not None else None
    if row is not None:
        return ACTION_CLASSES[row["action"]]
    fallback = "ServerError" if status >= 500 else "InvalidRequestError"
    return STATUS_CLASSES.get(status, fallback)


def retryable(status: int, body: Any) -> bool:
    """Whether a client retries a refusal: as a problem document's `retryable`
    says, or the website proxy's `retry`, and otherwise by its status."""
    if isinstance(body, dict):
        if "code" not in body and "error" in body:
            return body.get("retry") == _PROXY_RETRY
        if isinstance(body.get("retryable"), bool):
            return body["retryable"]
    return status in RETRYABLE_STATUSES


def retry_after(headers: Mapping[str, str]) -> float | None:
    """The seconds a `Retry-After` header asks for, or None."""
    try:
        wait = float(headers.get("retry-after", ""))
    except ValueError:
        return None
    return wait if wait >= 0 else None


POLL_BACKOFF_FIRST = 1.0
POLL_BACKOFF_CAP = 8.0


@dataclass
class PollPacing:
    """The wait before each poll of one long build: the `Retry-After` of a 429
    that refused the last poll, else the last operation resource's
    `retry_after_ms`, else the `Retry-After` of the response that carried that
    resource, else a backoff that doubles from 1 to 8 seconds and advances
    only when it is used."""

    paced: float | None = None
    header: float | None = None
    refused: float | None = None
    backoff_step: int = 0

    def took(self, operation: Mapping[str, Any], headers: Mapping[str, str]) -> None:
        """An operation resource arrived, with its response's headers."""
        ms = operation.get("retry_after_ms")
        number = isinstance(ms, int | float) and not isinstance(ms, bool)
        self.paced = ms / 1000 if number and ms >= 0 else None
        self.header = retry_after(headers)

    def rate_limited(self, headers: Mapping[str, str]) -> None:
        """A 429 refused the last poll; it spends no retry."""
        self.refused = retry_after(headers)

    def next_wait(self) -> float:
        if self.refused is not None:
            wait, self.refused = self.refused, None
            return wait
        if self.paced is not None:
            return self.paced
        if self.header is not None:
            return self.header
        wait = min(POLL_BACKOFF_FIRST * 2**self.backoff_step, POLL_BACKOFF_CAP)
        self.backoff_step += 1
        return wait


# The class of a client code that takes the class its refusal's HTTP status
# maps to, and the client code of a refusal whose body carries no code.
BY_STATUS = "by_status"
REFUSAL_WITHOUT_CODE = "refusal_without_code"


def error_outcome(error: str, code: str | None, **members: Any) -> dict[str, Any]:
    return {"error": {"class": error, "code": code, **members}}


# The warnings a client raises as errors under strict, which carry the
# server's code rather than a client code.
WARNING_CLASSES = ("SearchWarning", "DataWarning")
VERSION_UNAVAILABLE = "VersionUnavailableError"


class ClientError(Exception):
    """A step ends in an error a client raises. A refusal names its class and
    the server's code, a SearchWarning or DataWarning names the server's
    warning, departure or operation code, and any other error names a code
    from `client-codes.json`, whose row gives its class. A
    VersionUnavailableError also names the newest version, or None."""

    def __init__(
        self,
        code: str | None,
        *,
        error: str | None = None,
        newest_version: str | None = None,
    ) -> None:
        super().__init__(f"{error or 'client'} {code}")
        self.code = code
        self.error = error
        self.newest_version = newest_version

    def error_class(self, client_codes: Mapping[str, Mapping[str, Any]]) -> str:
        """The error class: the one it names, else its client code's.

        Raises:
            ValueError: The code is not a client code of one class.
        """
        if self.error is not None:
            return self.error
        row = client_codes.get(self.code) if self.code is not None else None
        if row is None or row["class"] == BY_STATUS:
            raise ValueError(
                f"{self.code!r} is not a client code of one class in client-codes.json"
            )
        return row["class"]

    def outcome(self, client_codes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """The error outcome, its class from `client_codes` when the error
        names none.

        Raises:
            ValueError: The code is not a client code of one class.
        """
        error = self.error_class(client_codes)
        if error == VERSION_UNAVAILABLE:
            return error_outcome(error, self.code, newest_version=self.newest_version)
        return error_outcome(error, self.code)


def refusal_error(
    status: int, body: Any, actions: Mapping[str, Mapping[str, Any]]
) -> ClientError:
    """The error a refusal raises: its code's class and the server's code, or
    for a body with no code the class its status maps to and
    `refusal_without_code`."""
    code = refusal_code(body)
    return ClientError(
        code or REFUSAL_WITHOUT_CODE, error=error_class(status, code, actions)
    )


# The answer kinds a client reads. Any other kind raises ContractError.
ANSWER_KINDS = ("resolution", "catalogue", "declined")
# The variable text an identity resubmit sends for a member no answer part names.
UNNAMED_VARIABLE_TEXT = "dataset"
# The warning severities a fetch reports in its SearchWarning; `info` stays quiet.
SHOWN_SEVERITIES = ("warn", "degraded")


@dataclass(frozen=True, slots=True)
class Answer:
    """A search answer as a client holds it: the response body, its parts,
    sentence and status, which a local choice replaces."""

    body: dict[str, Any]
    parts: list[dict[str, Any]]
    sentence: str | None
    status: str | None


def answer_from_body(body: dict[str, Any]) -> Answer:
    """The answer a search response holds; a kind no client knows raises
    ContractError."""
    if body.get("kind") not in ANSWER_KINDS:
        raise ClientError("contract_violation")
    return Answer(
        body, body.get("parts", []), body.get("rendered_sentence"), body.get("status")
    )


def candidate_answer(answer: Answer, part: int, spec: dict[str, Any]) -> Answer:
    """The answer a choice among one part's candidates makes with no request:
    that part definite on the chosen spec, keeping its other members, with
    that spec's sentence."""
    chosen = answer.parts[part]
    definite = {
        **{k: v for k, v in chosen.items() if k != "candidates"},
        "status": "definite",
        "spec": spec,
    }
    return Answer(
        answer.body, [definite], spec["resolved"]["rendered_sentence"], "definite"
    )


# --- choices ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Choice:
    """What a choose step settles: the part's index, and the chosen candidate
    spec or no_data option."""

    part: int
    candidate: dict[str, Any] | None = None
    option: dict[str, Any] | None = None


def _refuse() -> ClientError:
    return ClientError("choice_invalid")


def choice_for(
    answer: Answer, kinds: Mapping[str, str], part: int | str | None, option: int | str
) -> Choice:
    """The part and option a choose step names, or InvalidRequestError.

    With no part named, exactly one gap must await a choice: a `choice` gap,
    or any gap with options. A part is named by its index, its variable text,
    or a word that one gap's options mention, and must be a gap. A
    candidates part's options are its candidates, chosen by index; any other
    gap's are its no_data options, chosen by index or label, a label scoped to
    the options that mention the word the part was named by."""
    gaps = answer_gaps(answer, kinds)
    gap_parts = {gap["part"] for gap in gaps}
    mention = None
    if part is None:
        waiting = [g["part"] for g in gaps if g["kind"] == "choice" or g["options"]]
        if len(waiting) != 1:
            raise _refuse()
        index = waiting[0]
    elif isinstance(part, int):
        index = part
    else:
        named = [
            i for i, p in enumerate(answer.parts) if p.get("variable_text") == part
        ]
        if not named:
            mention = part
            named = [
                i
                for i in sorted(gap_parts)
                if any(part in o.get("mentions", []) for o in _options(answer, i))
            ]
        if len(named) != 1:
            raise _refuse()
        index = named[0]
    if index not in gap_parts:
        raise _refuse()

    chosen = answer.parts[index]
    if chosen.get("status") == "candidates":
        specs = chosen["candidates"]["specs"]
        if not isinstance(option, int):
            raise _UnfitError("candidates are chosen by index")
        if not 0 <= option < len(specs):
            raise _refuse()
        return Choice(index, candidate=specs[option])
    options = _options(answer, index)
    if isinstance(option, int):
        found = [options[option]] if 0 <= option < len(options) else []
    else:
        found = [
            o
            for o in options
            if o.get("label") == option
            and (mention is None or mention in o.get("mentions", []))
        ]
    if len(found) != 1:
        raise _refuse()
    return Choice(index, option=found[0])


def _options(answer: Answer, index: int) -> list[dict[str, Any]]:
    return answer.parts[index].get("no_data", {}).get("options", [])


def choice_input(answer: Answer, choice: Choice) -> dict[str, Any] | None:
    """The Search input a choice posts, or None for a choice among one part's
    candidates, which makes no request. In a one-part answer an option posts
    its data_query; in a several-part answer the choice posts
    dataset_resubmit with one entry per part, in part order."""
    if len(answer.parts) == 1:
        if choice.option is None:
            return None
        return choice.option["data_query"]
    entries = [
        resubmit_entry(part, choice if index == choice.part else None)
        for index, part in enumerate(answer.parts)
    ]
    return {"kind": "dataset_resubmit", "entries": entries}


def resubmit_entry(part: dict[str, Any], choice: Choice | None) -> dict[str, Any]:
    """One part's dataset_resubmit entry. The chosen candidate posts its
    identity and the chosen option its one parsed part. Otherwise a definite
    part posts its spec's identity, a candidates part its candidate set in the
    server's order with its ordering and truncated echoed, and any other part
    its restatement as a parsed entry, or a placeholder when it has none."""
    if choice is not None and choice.candidate is not None:
        return _echo(part, entry_kind="identity", **_identity(choice.candidate))
    if choice is not None and choice.option is not None:
        parts = choice.option["data_query"].get("parts")
        if not isinstance(parts, list) or len(parts) != 1:
            raise ClientError("contract_violation")
        return {"entry_kind": "parsed", "part": parts[0]}
    match part.get("status"):
        case "definite":
            return _echo(part, entry_kind="identity", **_identity(part["spec"]))
        case "candidates":
            candidates = part["candidates"]
            return _echo(
                part,
                entry_kind="candidates",
                ordering=candidates.get("ordering"),
                truncated=candidates.get("truncated") is True,
                candidates=[_identity(spec) for spec in candidates["specs"]],
            )
    return _restated_entry(part)


def _restated_entry(part: dict[str, Any]) -> dict[str, Any]:
    """A part without a reading: its restatement as a parsed entry, else a
    placeholder of its no_data reason or unsupported code. A part of unknown
    status without a restatement cannot be posted back."""
    if isinstance(part.get("restatement"), dict):
        return {"entry_kind": "parsed", "part": part["restatement"]}
    match part.get("status"):
        case "no_data":
            reason = part["no_data"]["reason"]
            return _echo(
                part, entry_kind="placeholder", status="no_data", reason=reason
            )
        case "unsupported":
            code = part["unsupported"]["code"]
            return _echo(
                part, entry_kind="placeholder", status="unsupported", code=code
            )
    raise _refuse()


def _identity(spec: dict[str, Any]) -> dict[str, Any]:
    """A spec's identity, and its entity groups when it has any."""
    item: dict[str, Any] = {"identity": spec["definition"]["identity"]}
    groups = spec.get("resolved", {}).get("entity_groups")
    if groups:
        item["entity_groups"] = groups
    return item


def _echo(part: dict[str, Any], **members: Any) -> dict[str, Any]:
    """An entry with the members every entry but a parsed one echoes from its
    part: variable_text, separate when true, and operations when any."""
    if not part.get("variable_text"):
        raise _refuse()
    entry: dict[str, Any] = {**members, "variable_text": part["variable_text"]}
    if part.get("separate") is True:
        entry["separate"] = True
    if part.get("operations"):
        entry["operations"] = part["operations"]
    return entry


def reading_hints(answer: Answer) -> dict[str, str]:
    """How `answer`'s readings differ from the question, each line a client
    shows once, with its code. In part order, each definite part gives each
    operation Search did not run, coded by its verb, each departure, and each
    warning shown at `warn` or `degraded` severity; the answer's diagnostics
    warnings follow. An `info` warning gives nothing."""
    hints: dict[str, str] = {}

    def notices(warnings: Any) -> None:
        for warning in warnings or []:
            if warning.get("severity") in SHOWN_SEVERITIES and warning.get("message"):
                hints.setdefault(f"Note: {warning['message']}", warning["code"])

    for part in answer.parts:
        if part.get("status") != "definite":
            continue
        for operation in part.get("operations") or []:
            line = f"Not done: {operation['verb']} ({json.dumps(operation['phrase'])})"
            hints.setdefault(line, operation["verb"])
        for departure in part.get("departures") or []:
            hints.setdefault(departure["text"], departure["code"])
        notices(part.get("warnings"))
    notices(answer.body.get("diagnostics", {}).get("warnings"))
    return hints


def search_warns(answer: Answer) -> bool:
    """Whether fetching `answer` issues a SearchWarning: its readings differ
    from the question in some way `reading_hints` lists."""
    return bool(reading_hints(answer))


def search_warning_code(answer: Answer) -> str | None:
    """The code a SearchWarning on `answer` carries: the one hint's code, or
    None when the readings differ in several ways."""
    codes = list(reading_hints(answer).values())
    return codes[0] if len(codes) == 1 else None


def answer_gaps(answer: Answer, kinds: Mapping[str, str]) -> list[dict[str, Any]]:
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
                    else kinds.get(no_data["reason"], "no_data")
                )
                options = len(no_data.get("options", []))
            case "unsupported":
                kind, options = "unsupported", 0
            case _:
                kind, options = "unknown", 0
        gaps.append({"part": index, "kind": kind, "options": options})
    return gaps


def answer_members(answer: Answer) -> list[tuple[list[str], dict[str, Any]]]:
    """One member per distinct spec_id of the definite parts, in part order:
    the variable text of every part it answers, and the first such part's
    spec."""
    members: dict[str, tuple[list[str], dict[str, Any]]] = {}
    for part in answer.parts:
        if part.get("status") != "definite":
            continue
        spec = part["spec"]
        members.setdefault(spec["spec_id"], ([], spec))[0].append(part["variable_text"])
    return list(members.values())


def answer_outcome(answer: Answer, kinds: Mapping[str, str]) -> dict[str, Any]:
    """An answer outcome. A declined or catalogue answer is never complete."""
    gaps = answer_gaps(answer, kinds)
    outcome: dict[str, Any] = {"kind": answer.body["kind"]}
    if answer.status is not None:
        outcome["status"] = answer.status
    outcome["sentence"] = answer.sentence
    outcome["complete"] = answer.body["kind"] == "resolution" and not gaps
    outcome["specs"] = [spec["spec_id"] for _, spec in answer_members(answer)]
    outcome["gaps"] = gaps
    return {"answer": outcome}


def part_label(member: Mapping[str, Any]) -> str:
    """The `part` column's value on a member's rows in a dataset of several
    members: the member's parts joined by `; `, or its sentence when it
    answers no part."""
    return "; ".join(member["parts"]) or member["sentence"]


def dataset_summary(
    members: Sequence[Mapping[str, Any]],
    missing: Sequence[Mapping[str, Any]],
    *,
    search_warning: bool | None = None,
) -> dict[str, Any]:
    """A dataset outcome's members and gaps, and for a fetch of an answer
    whether it issued a SearchWarning, without the rows, columns and per-part
    row counts the Parquet bodies hold."""
    summary: dict[str, Any] = {
        "complete": not missing,
        "members": [
            {
                "parts": list(member["parts"]),
                "spec_id": member["spec_id"],
                "content_version": member["content_version"],
                "sentence": member["sentence"],
            }
            for member in members
        ],
        "missing": [{"part": gap["part"], "kind": gap["kind"]} for gap in missing],
    }
    if search_warning is not None:
        summary["search_warning"] = search_warning
    return summary


def _strict_members(strict: dict[str, Any]) -> dict[str, list[str]]:
    """The required strict members of each component schema."""
    members: dict[str, list[str]] = {}
    for entry in strict["required"]:
        match = _STRICT_POINTER.match(entry["pointer"])
        if match is not None:
            members.setdefault(match.group(1), []).append(match.group(2))
    return members


def strict_gaps(
    openapi: dict[str, Any],
    members: Mapping[str, Sequence[str]],
    schema: Any,
    body: Any,
    where: str = "",
) -> list[str]:
    """The strict members `body` lacks or holds as null, read through
    `schema`: each component schema it reaches names its own, and a
    discriminated union whose discriminator `body` lacks is a gap. A
    discriminator value the union does not map, and a union without a
    discriminator that offers more than one object branch, reach nothing."""
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        gaps = []
        if isinstance(body, dict):
            gaps = [
                f"{where}/{name}"
                for name in members.get(ref.rsplit("/", 1)[-1], ())
                if body.get(name) is None
            ]
        return gaps + strict_gaps(
            openapi, members, _resolve(openapi, ref[1:]), body, where
        )
    discriminator = schema.get("discriminator")
    if isinstance(discriminator, dict) and isinstance(body, dict):
        name = discriminator["propertyName"]
        if body.get(name) is None:
            return [f"{where}/{name}"]
        target = discriminator.get("mapping", {}).get(body[name])
        if target is None:
            return []
        return strict_gaps(openapi, members, {"$ref": target}, body, where)
    gaps = []
    for keyword in ("anyOf", "oneOf"):
        branches = [b for b in schema.get(keyword, []) if b.get("type") != "null"]
        if len(branches) == 1:
            gaps.extend(strict_gaps(openapi, members, branches[0], body, where))
    for branch in schema.get("allOf", []):
        gaps.extend(strict_gaps(openapi, members, branch, body, where))
    if isinstance(body, dict):
        for name, member in schema.get("properties", {}).items():
            if name in body:
                gaps.extend(
                    strict_gaps(openapi, members, member, body[name], f"{where}/{name}")
                )
    if isinstance(body, list) and "items" in schema:
        for index, item in enumerate(body):
            gaps.extend(
                strict_gaps(openapi, members, schema["items"], item, f"{where}/{index}")
            )
    return gaps


class _UnfitError(Exception):
    """The exchanges do not fit the steps, so no outcome follows from them."""


_SEARCH = ("POST", "/search/resolve")
_BUILD = ("POST", "/datasets")
_POLL = ("GET", "/datasets/operations/")
_MANIFEST = {"$ref": "#/components/schemas/Manifest"}
_OPERATION = {"$ref": "#/components/schemas/OperationResource"}
# The bare Parquet rendering a fetch asks for, by layout.
LAYOUT_RENDERINGS = {
    "long": {"format": "parquet", "bundle": False},
    "frame": {"format": "parquet", "bundle": False, "layout": "frame"},
}
# The snapshot refusal after which a stored version's lookup builds it again:
# the version exists, but its file has expired. Any other refusal ends it.
FILE_EXPIRED = "artifact_expired"
# The file route refusals after which the lookup takes its next step.
FILE_GONE = frozenset({FILE_EXPIRED, "artifact_not_found", "not_found"})
# The classes of a rebuild refusal after which the lookup reads the newest
# version, and of a versions refusal that leaves the newest version unknown.
REBUILD_REFUSED = frozenset(
    {"InvalidRequestError", "NotFoundError", VERSION_UNAVAILABLE}
)
NO_VERSIONS = frozenset({"NotFoundError", "PermissionDeniedError"})
OPERATION_FAILED = "operation_failed"


def rendering_for(layout: str | None) -> dict[str, Any]:
    """The build request's `options` for a fetch's `layout`, long by default."""
    return dict(LAYOUT_RENDERINGS[layout or "long"])


def pick_artifact(
    manifest: Mapping[str, Any], rendering: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The artifact a manifest lists for `rendering`, matched on format,
    bundle and whether its layout is the frame."""
    frame = rendering.get("layout") == "frame"
    for artifact in manifest["artifacts"]:
        found = artifact.get("rendering")
        if (
            isinstance(found, dict)
            and found.get("format") == rendering["format"]
            and found.get("bundle") is rendering["bundle"]
            and (found.get("layout") == "frame") == frame
        ):
            return artifact
    return None


def digest_of(data: bytes) -> str:
    """The `sha256:<hex>` digest the server names a stored file by."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def selector_path(
    spec_id: str, content_version: str, rendering: Mapping[str, Any]
) -> str:
    """The file selector's path for a stored version, its rendering as the
    query."""
    query = "&".join(
        f"{name}={str(value).lower() if isinstance(value, bool) else value}"
        for name, value in rendering.items()
    )
    return f"/datasets/{spec_id}/{content_version}/file?{query}"


def data_warning(manifest: Mapping[str, Any]) -> ClientError | None:
    """The DataWarning a strict fetch raises for a manifest: its first warning
    above `info` severity, with that warning's code; None when it has none."""
    for warning in manifest.get("warnings") or []:
        if isinstance(warning, dict) and warning.get("severity") != "info":
            return ClientError(warning.get("code"), error="DataWarning")
    return None


def resubmitted_sentence(answer: Answer, spec_id: str) -> str:
    """The sentence Search gives a built identity it was posted: the answer
    must be one definite part of `spec_id`, with a sentence, or the client
    raises ContractError."""
    found = answer.parts[0] if len(answer.parts) == 1 else {}
    spec = found.get("spec") if found.get("status") == "definite" else None
    if answer.body.get("kind") != "resolution" or not isinstance(spec, dict):
        raise ClientError("contract_violation")
    resolved = spec.get("resolved")
    sentence = resolved.get("rendered_sentence") if isinstance(resolved, dict) else None
    if spec.get("spec_id") != spec_id or not isinstance(sentence, str) or not sentence:
        raise ClientError("contract_violation")
    return sentence


def failed_operation(
    resource: Mapping[str, Any], actions: Mapping[str, Mapping[str, Any]]
) -> tuple[ClientError, bool]:
    """A failed operation's error, the refusal its problem document states at
    that document's status or else 500, and whether it is retryable."""
    problem = resource.get("error")
    status = problem.get("status") if isinstance(problem, dict) else None
    status = status if isinstance(status, int) else 500
    return refusal_error(status, problem, actions), retryable(status, problem)


class LongBuild:
    """A build the server runs on, followed the way a client polls it. Each
    wait before a poll is `PollPacing`'s. A 429 poll spends no retry, any
    other retryable refused poll one of MAX_RETRIES, and any other refused
    poll raises. A poll answering a failed operation, retryable by its
    error, posts the build again once when the client holds its request;
    otherwise, and for an operation that failed before the client polled
    it, the operation's error raises. `entry` is the build's `long_builds`
    entry, and `result` its manifest once it is done."""

    def __init__(
        self,
        resource: Mapping[str, Any],
        headers: Mapping[str, str],
        request: Any,
        actions: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.request = request
        self.actions = actions
        self.pacing = PollPacing()
        self.retries = 0
        self.result: dict[str, Any] | None = None
        self.entry: dict[str, Any] = {
            "operation_id": resource["operation"]["operation_id"],
            "poll_waits": [],
            "resubmissions": 0,
        }
        self._take(resource, headers)

    def _take(self, resource: Mapping[str, Any], headers: Mapping[str, str]) -> None:
        self.resource = resource
        self.pacing.took(resource["operation"], headers)
        if resource["operation"]["status"] == "done":
            manifest = resource.get("manifest")
            if not isinstance(manifest, dict):
                raise ClientError("contract_violation")
            self.result = manifest

    def next_wait(self) -> float:
        """The wait before the next poll, kept in `entry`."""
        if self.resource["operation"]["status"] == "failed":
            raise failed_operation(self.resource, self.actions)[0]
        wait = self.pacing.next_wait()
        self.entry["poll_waits"].append(wait)
        return wait

    def polled(self, status: int, headers: Mapping[str, str], body: Any) -> bool:
        """Takes a poll's response, and says whether to post the build again.

        Raises:
            ClientError: The error that ends the build.
        """
        if status < 400:
            self.retries = 0
            self._take(body, headers)
            if body["operation"]["status"] != "failed":
                return False
            error, again = failed_operation(body, self.actions)
        else:
            error = refusal_error(status, body, self.actions)
            again = retryable(status, body)
            if error.code != OPERATION_FAILED:
                if status == 429:
                    self.pacing.rate_limited(headers)
                elif again and self.retries < MAX_RETRIES:
                    self.retries += 1
                else:
                    raise error
                return False
        if not again or self.request is None or self.entry["resubmissions"]:
            raise error
        self.entry["resubmissions"] += 1
        return True

    def reposted(self, status: int, headers: Mapping[str, str], body: Any) -> None:
        """Takes the answer to the build posted again: a refusal raises, a
        202 starts the operation the client polls next, and a 200 is done."""
        if status >= 400:
            raise refusal_error(status, body, self.actions)
        if status != 202:
            self.result = body
            return
        self.entry["operation_id"] = body["operation"]["operation_id"]
        self._take(body, headers)


@dataclass
class _Minted:
    """An operation a mint started or a resume read, for the result step:
    its resource, the headers that came with it, the build request a failed
    operation resubmits (None for a resumed one), the spec the client holds,
    and the rendering it downloads."""

    resource: dict[str, Any]
    headers: dict[str, str]
    request: dict[str, Any] | None
    spec: dict[str, Any] | None
    rendering: dict[str, Any]


@dataclass
class _Replay:
    """A record's steps run over its exchanges the way a client makes them,
    each giving the outcome it reaches."""

    exchanges: list[dict[str, Any]]
    routes: _Routes
    base_path: str
    members: Mapping[str, Sequence[str]]
    actions: Mapping[str, Mapping[str, Any]]
    client_codes: Mapping[str, Mapping[str, Any]]
    body_dir: Path
    cursor: int = 0
    answer: Answer | None = None
    minted: _Minted | None = None
    long_builds: list[dict[str, Any]] = field(default_factory=list)

    @property
    def kinds(self) -> dict[str, str]:
        return gap_kinds(self.actions)

    # -- exchanges --

    def call(self, method: str, target: str, *, retries: bool = True) -> dict[str, Any]:
        """The exchange a client acts on for its next request: with `retries`,
        the last of a run that repeats one request, each refusal before it a
        retry. `target` is a path under the base URL, a path prefix ending in
        `/`, `origin` for a root-relative URL or `url` for an absolute one."""
        if self.cursor >= len(self.exchanges):
            raise _UnfitError(f"the steps make a {method} {target} the record lacks")
        exchange = self.exchanges[self.cursor]
        request = exchange["request"]
        actual = request.get("path") or (
            "origin" if "origin_path" in request else "url"
        )
        fits = request["method"] == method and (
            actual == target or (target.endswith("/") and actual.startswith(target))
        )
        if not fits:
            raise _UnfitError(
                f"exchange {self.cursor} is {request['method']} {actual}, where the "
                f"steps make a {method} {target}"
            )
        self.cursor += 1
        while (
            retries
            and exchange["response"]["status"] >= 400
            and self.cursor < len(self.exchanges)
            and self.exchanges[self.cursor]["request"] == request
        ):
            exchange = self.exchanges[self.cursor]
            self.cursor += 1
        return exchange

    def acted_on(self, exchange: dict[str, Any], schema: Any = None) -> Any:
        """The JSON body a client acts on, read through `schema` or else its
        route's schema for its status. A refusal raises its error, and a body
        without a strict member raises ContractError."""
        response = exchange["response"]
        body = response.get("json")
        if response["status"] >= 400:
            raise refusal_error(response["status"], body, self.actions)
        if schema is None:
            schema = self.routes.response_schema(
                self.base_path, exchange["request"], response["status"]
            )
        self.strict(schema, body)
        return body

    def strict(self, schema: Any, body: Any) -> None:
        if strict_gaps(self.routes.openapi, self.members, schema, body):
            raise ClientError("contract_violation")

    def body(self, response: dict[str, Any]) -> bytes:
        if "body_file" in response:
            path = self.body_dir / response["body_file"]
            if not path.is_file():
                raise _UnfitError(f"body file {response['body_file']!r} is missing")
            return path.read_bytes()
        if "json" in response:
            return json.dumps(response["json"]).encode()
        return b""

    def download(self, target: str, digest: str, *, origin: bool) -> None:
        """A file through the route that redirects to its signed URL: an
        artifact's root-relative `url` when `origin`, else a path under the
        base URL. A refused signed URL, or bytes that do not hash to `digest`,
        ask the route once more, and a second failure raises."""
        failure = ClientError("download_failed")
        for _ in range(2):
            exchange = self.call("GET", "origin" if origin else target)
            asked = exchange["request"].get("origin_path", target)
            if asked != target:
                raise _UnfitError(
                    f"the record asks {asked}, where the rules ask {target}"
                )
            self.acted_on(exchange)
            signed = self.call("GET", "url", retries=False)["response"]
            if signed["status"] >= 400:
                failure = ClientError("download_failed")
                continue
            if digest_of(self.body(signed)) == digest:
                return
            failure = ClientError("digest_mismatch")
        raise failure

    def resolve(self, posts: dict[str, Any]) -> Answer:
        """A Search request whose recorded body, when it has one, posts the
        input `posts`."""
        exchange = self.call(*_SEARCH)
        sent = exchange["request"].get("json")
        if sent is not None and sent.get("input") != posts:
            raise _UnfitError(
                f"exchange {self.cursor - 1} posts {json.dumps(sent.get('input'))[:300]}"
                f", where the rules post {json.dumps(posts)[:300]}"
            )
        return answer_from_body(self.acted_on(exchange))

    # -- builds --

    def build(self, rendering: dict[str, Any]) -> dict[str, Any]:
        """A build of the latest version, posting `rendering` as its options:
        the manifest it answers, through a long build when it answers 202."""
        exchange = self.call(*_BUILD)
        sent = exchange["request"].get("json")
        if sent is not None and sent.get("options") != rendering:
            raise _UnfitError(
                f"exchange {self.cursor - 1} posts options {sent.get('options')}, "
                f"where the rules post {rendering}"
            )
        if exchange["response"]["status"] != 202:
            return self.acted_on(exchange)
        resource = self.acted_on(exchange, _OPERATION)
        headers = exchange["response"].get("headers", {})
        return self.long_build(resource, headers, exchange["request"])

    def long_build(
        self,
        resource: dict[str, Any],
        headers: Mapping[str, str],
        request: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Polls an operation as `LongBuild` rules until it is done and
        returns its manifest. A build posted again posts the same request."""
        build = LongBuild(resource, headers, request, self.actions)
        self.long_builds.append(build.entry)
        while build.result is None:
            build.next_wait()
            response = self.call(*_POLL, retries=False)["response"]
            body = response.get("json")
            if response["status"] < 400:
                self.strict(_OPERATION, body)
            if not build.polled(response["status"], response.get("headers", {}), body):
                continue
            again = self.call(*_BUILD)
            if again["request"] != request:
                raise _UnfitError("the build posted again posts another request")
            reply = again["response"]
            if reply["status"] < 400:
                self.strict(
                    _OPERATION if reply["status"] == 202 else _MANIFEST,
                    reply.get("json"),
                )
            build.reposted(reply["status"], reply.get("headers", {}), reply.get("json"))
        self.strict(_MANIFEST, build.result)
        return build.result

    def note_warnings(self, manifest: dict[str, Any], strict: bool) -> None:
        if strict and (warning := data_warning(manifest)) is not None:
            raise warning

    def fetched(
        self,
        parts: list[str],
        spec: dict[str, Any],
        rendering: dict[str, Any],
        strict: bool,
    ) -> dict[str, Any]:
        """One member: the build, then the member's files."""
        return self.member(parts, spec, self.build(rendering), rendering, strict)

    def member(
        self,
        parts: list[str],
        spec: dict[str, Any] | None,
        manifest: dict[str, Any],
        rendering: dict[str, Any],
        strict: bool,
    ) -> dict[str, Any]:
        """A built member: its warnings under strict, its sentence from
        Search when the build changed the spec_id or the client lacks it,
        then the download. `spec` is None for a resumed operation."""
        self.note_warnings(manifest, strict)
        artifact = pick_artifact(manifest, rendering)
        if artifact is None:
            raise ClientError("contract_violation")
        built = manifest["definition"]["spec_id"]
        sentence = None if spec is None else spec.get("sentence")
        if spec is None or built != spec["spec_id"] or sentence is None:
            sentence = self.sentence(parts, manifest["definition"])
        self.download(artifact["url"], artifact["artifact_digest"], origin=True)
        return {
            "parts": parts,
            "spec_id": built,
            "content_version": manifest["content_version"],
            "sentence": sentence,
        }

    def sentence(self, parts: list[str], definition: dict[str, Any]) -> str:
        """Search's sentence for a built identity. An answer that is not one
        definite part of the built spec_id, with a sentence, raises
        ContractError."""
        entry = {
            "entry_kind": "identity",
            "variable_text": parts[0] if parts else UNNAMED_VARIABLE_TEXT,
            "identity": definition["identity"],
        }
        answer = self.resolve({"kind": "dataset_resubmit", "entries": [entry]})
        return resubmitted_sentence(answer, definition["spec_id"])

    # -- stored versions --

    def stored_version(self, member: dict[str, Any]) -> dict[str, Any]:
        """One version a ref names, through the lookup order: the snapshot
        route and the files route at the stored digest, then the file
        selector, then a build that must answer the same version. A snapshot
        refusal other than artifact_expired ends the lookup; otherwise a
        lookup that finds no file raises VersionUnavailableError with the
        first refusal's code, or version_unavailable, and the newest version:
        the one the build answered, or the versions route's first."""
        spec = member["spec"]
        spec_id, version = spec["spec_id"], member["content_version"]
        rendering = member["artifact"]["rendering"]
        stored = {
            "parts": member["parts"],
            "spec_id": spec_id,
            "content_version": version,
            "sentence": member["sentence"],
        }
        refusal: ClientError | None = None
        try:
            manifest = self.acted_on(self.call("GET", f"/datasets/{spec_id}/{version}"))
        except ClientError as error:
            if error.code != FILE_EXPIRED:
                raise
            refusal, manifest = error, None
        if manifest is not None:
            built = (manifest["definition"]["spec_id"], manifest["content_version"])
            if built != (spec_id, version):
                raise ClientError("contract_violation")
            found, refusal = self.stored_file(member, manifest)
            if found:
                return stored

        try:
            manifest = self.build(rendering)
        except ClientError as error:
            if error.error_class(self.client_codes) not in REBUILD_REFUSED:
                raise
            refusal = refusal if refusal is not None else error
            newest = self.newest(spec_id)
        else:
            if manifest["content_version"] == version:
                held = {"spec_id": spec_id, "sentence": member["sentence"]}
                return self.member(member["parts"], held, manifest, rendering, False)
            newest = manifest["content_version"]
        raise ClientError(
            refusal.code if refusal is not None else "version_unavailable",
            error=VERSION_UNAVAILABLE,
            newest_version=newest,
        )

    def stored_file(
        self, member: dict[str, Any], manifest: dict[str, Any]
    ) -> tuple[bool, ClientError | None]:
        """Whether the files route at the stored digest, or else the file
        selector, served the stored file, and the files route's refusal when
        it refused. A selector refusal outside FILE_GONE, and a files route
        one, raise; a selector file of another digest is not the stored one."""
        digest = member["artifact"]["digest"]
        refusal = None
        artifact = next(
            (a for a in manifest["artifacts"] if a["artifact_digest"] == digest), None
        )
        if artifact is not None:
            try:
                self.download(artifact["url"], digest, origin=True)
            except ClientError as error:
                if error.code not in FILE_GONE:
                    raise
                refusal = error
            else:
                return True, None
        path = selector_path(
            member["spec"]["spec_id"],
            member["content_version"],
            member["artifact"]["rendering"],
        )
        try:
            self.download(path, digest, origin=False)
        except ClientError as error:
            if error.code == "digest_mismatch":
                return False, refusal
            if error.code in FILE_GONE:
                return False, refusal or error
            raise
        return True, None

    def newest(self, spec_id: str) -> str | None:
        """The newest stored version the versions route lists first, or None
        when it lists none or refuses as unknown or forbidden."""
        try:
            body = self.acted_on(self.call("GET", f"/datasets/{spec_id}/versions"))
        except ClientError as error:
            if error.error_class(self.client_codes) in NO_VERSIONS:
                return None
            raise
        versions = body.get("versions") if isinstance(body, dict) else None
        first = versions[0] if isinstance(versions, list) and versions else None
        newest = first.get("content_version") if isinstance(first, dict) else None
        return newest if isinstance(newest, str) else None

    # -- steps --

    def step(self, step: dict[str, Any]) -> dict[str, Any]:
        """The outcome `step` reaches. A step that made a long build names
        each in `long_builds`, whatever its outcome."""
        self.long_builds = []
        op = step["op"]
        if op == "fetch":
            op = (
                "fetch_ref" if "ref" in step else "fetch_spec" if "spec" in step else op
            )
        try:
            outcome = getattr(self, f"_{op}")(step)
        except ClientError as ended:
            if op in {"search", "choose"}:
                self.answer = None
            outcome = ended.outcome(self.client_codes)
        if self.long_builds:
            (value,) = outcome.values()
            value["long_builds"] = self.long_builds
        return outcome

    def _answered(self) -> Answer:
        if self.answer is None:
            raise _UnfitError("the step follows no answer")
        return self.answer

    def _search(self, step: dict[str, Any]) -> dict[str, Any]:
        self.answer = self.resolve({"kind": "raw_text", "query": step["question"]})
        return answer_outcome(self.answer, self.kinds)

    def _choose(self, step: dict[str, Any]) -> dict[str, Any]:
        answer = self._answered()
        choice = choice_for(answer, self.kinds, step.get("part"), step["option"])
        posts = choice_input(answer, choice)
        if posts is not None:
            chosen = self.resolve(posts)
        elif choice.candidate is not None:
            chosen = candidate_answer(answer, choice.part, choice.candidate)
        else:
            raise _UnfitError("a choice posts unless it picks a candidate")
        self.answer = chosen
        return answer_outcome(chosen, self.kinds)

    def _fetch(self, step: dict[str, Any]) -> dict[str, Any]:
        """A fetch of the answer: a declined or catalogue answer raises
        DeclinedError; a gap raises IncompleteAnswerError unless the fetch is
        partial and some part has a reading; a strict fetch whose readings
        depart from the question raises SearchWarning; the frame layout with
        several members raises InvalidRequestError; all before any request."""
        answer = self._answered()
        if answer.body.get("kind") != "resolution":
            raise ClientError("answer_declined")
        gaps = answer_gaps(answer, self.kinds)
        readings = answer_members(answer)
        if gaps and (not step.get("partial", False) or not readings):
            raise ClientError("answer_incomplete")
        warns = search_warns(answer)
        strict = step.get("strict", False)
        if warns and strict:
            raise ClientError(search_warning_code(answer), error="SearchWarning")
        rendering = rendering_for(step.get("layout"))
        if "layout" in rendering and len(readings) > 1:
            raise ClientError("invalid_argument")
        members = [
            self.fetched(
                parts,
                {
                    "spec_id": spec["spec_id"],
                    "sentence": spec["resolved"]["rendered_sentence"],
                },
                rendering,
                strict,
            )
            for parts, spec in readings
        ]
        return {"dataset": dataset_summary(members, gaps, search_warning=warns)}

    def _fetch_spec(self, step: dict[str, Any]) -> dict[str, Any]:
        rendering = rendering_for(step.get("layout"))
        member = self.fetched([], step["spec"], rendering, step.get("strict", False))
        return {"dataset": dataset_summary([member], [])}

    def _fetch_ref(self, step: dict[str, Any]) -> dict[str, Any]:
        members = [self.stored_version(member) for member in step["ref"]["members"]]
        return {"dataset": dataset_summary(members, [])}

    def _mint(self, step: dict[str, Any]) -> dict[str, Any]:
        rendering = rendering_for(step.get("layout"))
        exchange = self.call(*_BUILD)
        if exchange["response"]["status"] == 200:
            raise _UnfitError("a mint step's build answers an operation")
        resource = self.acted_on(exchange, _OPERATION)
        headers = exchange["response"].get("headers", {})
        self.minted = _Minted(
            resource, headers, exchange["request"], step["spec"], rendering
        )
        return _operation_outcome(resource)

    def _operation(self, step: dict[str, Any]) -> dict[str, Any]:
        exchange = self.call("GET", f"/datasets/operations/{step['operation_id']}")
        resource = self.acted_on(exchange, _OPERATION)
        headers = exchange["response"].get("headers", {})
        rendering = rendering_for(step.get("layout"))
        self.minted = _Minted(resource, headers, None, None, rendering)
        return _operation_outcome(resource)

    def _result(self, step: dict[str, Any]) -> dict[str, Any]:
        if self.minted is None:
            raise _UnfitError("result follows no mint or operation")
        minted = self.minted
        manifest = self.long_build(minted.resource, minted.headers, minted.request)
        member = self.member([], minted.spec, manifest, minted.rendering, False)
        return {"dataset": dataset_summary([member], [])}


def _operation_outcome(resource: dict[str, Any]) -> dict[str, Any]:
    operation = resource["operation"]
    return {"operation": {k: operation[k] for k in ("operation_id", "status")}}


def expected_outcomes(
    record: dict[str, Any],
    *,
    routes: _Routes,
    base_path: str,
    strict: dict[str, Any],
    actions: Mapping[str, Mapping[str, Any]],
    client_codes: Mapping[str, Mapping[str, Any]],
    body_dir: Path,
) -> list[dict[str, Any]]:
    """The outcome each of `record`'s steps reaches over its exchanges, its
    binary bodies read from `body_dir`, over
    `actions` for refusals' error classes and gap kinds, and over
    `client_codes` for the class of each error the client raises itself. A
    dataset outcome holds what `dataset_summary` gives, without rows and
    columns.

    Raises:
        ValueError: The exchanges do not fit the steps.
    """
    replay = _Replay(
        record["exchanges"],
        routes,
        base_path,
        _strict_members(strict),
        actions,
        client_codes,
        body_dir,
    )
    try:
        outcomes = [replay.step(step) for step in record["call"]]
    except (_UnfitError, KeyError, IndexError, StopIteration, ValueError) as exc:
        raise ValueError(f"the exchanges do not fit the steps: {exc!r}") from exc
    if replay.cursor != len(replay.exchanges):
        raise ValueError(
            f"the steps never make exchanges {replay.cursor} to "
            f"{len(replay.exchanges) - 1}"
        )
    return outcomes


def owns_body_file(name: str, body_file: str) -> bool:
    """Whether `body_file` is one the scenario `name` writes: `<name>.<ext>`,
    or `<name>-<n>.<ext>` for its n-th body."""
    return (
        re.fullmatch(rf"{re.escape(name)}(-[0-9]+)?\.[a-z0-9]+", body_file) is not None
    )


_REDACTED_QUERY = "X-Goog-Signature=redacted"


def _check_redaction(label: str, exchange: dict[str, Any]) -> list[str]:
    """An absolute URL a record holds, a `url` request or a `location`
    header, keeps no query but the redacted signature."""
    urls = [
        exchange["request"].get("url"),
        exchange["response"].get("headers", {}).get("location"),
    ]
    return [
        f"{label}: {url.split('?', 1)[0]} keeps a query other than {_REDACTED_QUERY}"
        for url in urls
        if isinstance(url, str)
        and urlsplit(url).netloc
        and urlsplit(url).query != _REDACTED_QUERY
    ]


def _check_body_files(
    file_name: str,
    record: dict[str, Any],
    parent: dict[str, Any] | None,
    directory: Path,
) -> list[str]:
    """A body file is the scenario's own, or for a synthetic scenario one
    its parent names, and it exists."""
    inherited = (
        {e["response"].get("body_file") for e in parent["exchanges"]}
        if parent
        else set()
    )
    problems = []
    for index, exchange in enumerate(record["exchanges"]):
        body_file = exchange["response"].get("body_file")
        if body_file is None:
            continue
        if not owns_body_file(record["name"], body_file) and body_file not in inherited:
            problems.append(
                f"scenarios/{file_name}: exchange {index}: body file {body_file!r} is "
                "neither the scenario's own nor one its parent names"
            )
        if not (directory / body_file).is_file():
            problems.append(
                f"scenarios/{file_name}: exchange {index}: body file "
                f"{body_file!r} is missing"
            )
    return problems


def _check_expect(
    file_name: str,
    record: dict[str, Any],
    derived: list[dict[str, Any]],
) -> list[str]:
    """Each outcome in `expect` against the one its exchanges give. A dataset
    outcome's rows, columns and per-part row counts are the Parquet bodies'
    and are not derived, except that several members put the `part` column
    first and list each member's `part` value, rows summing to the dataset's."""
    problems = []
    for index, (stated, wanted) in enumerate(
        zip(record["expect"], derived, strict=False)
    ):
        compared = stated
        if "dataset" in stated and "dataset" in wanted:
            dataset = stated["dataset"]
            compared = {
                "dataset": {
                    k: v
                    for k, v in dataset.items()
                    if k not in {"rows", "columns", "part_rows"}
                }
            }
            problems.extend(
                f"scenarios/{file_name}: step {index}: {problem}"
                for problem in _part_problems(dataset, wanted["dataset"]["members"])
            )
        if compared != wanted:
            problems.append(
                f"scenarios/{file_name}: step {index} expects "
                f"{json.dumps(compared)[:300]}, but its exchanges give "
                f"{json.dumps(wanted)[:300]}"
            )
    return problems


def _part_problems(
    dataset: dict[str, Any], members: Sequence[Mapping[str, Any]]
) -> list[str]:
    """A dataset of several members has the `part` column first and a
    `part_rows` entry per member, in member order, whose `part` is that
    member's `part_label` and whose rows sum to the dataset's; a dataset of
    one member has neither."""
    several = len(members) > 1
    problems = []
    if (dataset["columns"][:1] == ["part"]) != several:
        problems.append(
            "the part column comes first exactly when the dataset has several members"
        )
    part_rows = dataset.get("part_rows")
    if (part_rows is not None) != several:
        problems.append(
            "part_rows is given exactly when the dataset has several members"
        )
        return problems
    if part_rows is None:
        return problems
    labels = [part_label(member) for member in members]
    if [entry["part"] for entry in part_rows] != labels:
        problems.append(
            f"part_rows names {[entry['part'] for entry in part_rows]}, but the "
            f"members' part values are {labels}"
        )
    if sum(entry["rows"] for entry in part_rows) != dataset["rows"]:
        problems.append("part_rows' rows do not sum to the dataset's rows")
    return problems


def _check_scenarios(
    validator: jsonschema.Draft202012Validator,
    openapi: dict[str, Any],
    *,
    registry: dict[str, Any],
    strict: dict[str, Any],
    actions: Mapping[str, Mapping[str, Any]],
    client_codes: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    directory = PACKAGE_DIR / "scenarios"
    paths = sorted(directory.glob("*.json"))
    problems = [] if paths else ["scenarios: no scenario records found"]
    routes = _Routes(openapi)
    records: dict[str, dict[str, Any]] = {}

    for path in paths:
        try:
            record = _load(path)
        except ValueError as exc:
            problems.append(f"scenarios/{path.name}: not valid JSON: {exc}")
            continue
        errors = _schema_problems(f"scenarios/{path.name}", validator, record)
        problems.extend(errors)
        if errors:
            continue
        name = record["name"]
        if name != path.stem:
            problems.append(f"scenarios/{path.name}: name {name!r} differs")
        if name in records:
            problems.append(f"scenarios/{path.name}: name {name!r} repeats")
        records[name] = record
        problems.extend(_check_steps(path.name, record))
        problems.extend(_check_client_errors(path.name, record, client_codes))
        for index, exchange in enumerate(record["exchanges"]):
            problems.extend(
                _check_redaction(f"scenarios/{path.name}: exchange {index}", exchange)
            )

    named_files: set[str] = set()
    for name, record in records.items():
        file_name = f"{name}.json"
        origin = record["origin"]
        parent = None
        if origin["kind"] == "synthetic":
            parent = records.get(origin["derived_from"])
            if parent is None or parent["origin"]["kind"] != "recorded":
                problems.append(
                    f"scenarios/{file_name}: derived_from {origin['derived_from']!r} "
                    "is not a recorded scenario"
                )
                continue
        named_files.update(
            e["response"]["body_file"]
            for e in record["exchanges"]
            if "body_file" in e["response"]
        )
        problems.extend(_check_body_files(file_name, record, parent, directory))
        recorded = parent or record
        base_path = urlsplit(recorded["origin"]["base_url"]).path.rstrip("/")
        if origin["kind"] == "recorded":
            for index, exchange in enumerate(record["exchanges"]):
                problems.extend(
                    _check_exchange(
                        f"scenarios/{file_name}: exchange {index}",
                        exchange,
                        base_path,
                        routes,
                        registry,
                    )
                )
        try:
            derived = expected_outcomes(
                record,
                routes=routes,
                base_path=base_path,
                strict=strict,
                actions=actions,
                client_codes=client_codes,
                body_dir=directory,
            )
        except ValueError as exc:
            problems.append(f"scenarios/{file_name}: {exc}")
            continue
        problems.extend(_check_expect(file_name, record, derived))

    problems.extend(
        f"scenarios/{p.name}: no scenario names this body file"
        for p in sorted(directory.iterdir())
        if p.is_file() and p.suffix != ".json" and p.name not in named_files
    )
    return problems


# The classes a refusal's HTTP status maps to.
_STATUS_ONLY_CLASSES = {*STATUS_CLASSES.values(), "ServerError", "InvalidRequestError"}


def _check_client_errors(
    file_name: str,
    record: dict[str, Any],
    client_codes: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Each error outcome no refusal in the record carries the code of names
    a client code of its class. A refusal is a response of status 400 or
    more, or a failed operation's error. A SearchWarning or DataWarning
    carries the server's code."""
    refused = {
        refusal_code(e["response"].get("json"))
        for e in record["exchanges"]
        if e["response"]["status"] >= 400
    } | {refusal_code(body.get("error")) for body in _failed_operations(record)}
    problems = []
    for index, outcome in enumerate(record["expect"]):
        error = outcome.get("error")
        if error is None or error["class"] in WARNING_CLASSES:
            continue
        code = error["code"]
        if code in refused:
            continue
        label = f"scenarios/{file_name}: step {index}"
        row = client_codes.get(code) if code is not None else None
        if row is None:
            problems.append(
                f"{label}: {error['class']} code {code!r} is neither a refusal's "
                "code in the exchanges nor a client code"
            )
        elif row["class"] == BY_STATUS:
            if error["class"] not in _STATUS_ONLY_CLASSES:
                problems.append(
                    f"{label}: {code!r} raises the class a status maps to, which "
                    f"{error['class']} is not"
                )
        elif row["class"] != error["class"]:
            problems.append(
                f"{label}: client code {code!r} raises {row['class']}, not "
                f"{error['class']}"
            )
    return problems


def _failed_operations(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Each operation resource in the record's responses whose status is
    failed."""
    for exchange in record["exchanges"]:
        body = exchange["response"].get("json")
        operation = body.get("operation") if isinstance(body, dict) else None
        if isinstance(operation, dict) and operation.get("status") == "failed":
            yield body


def _check_steps(file_name: str, record: dict[str, Any]) -> list[str]:
    call, expect = record["call"], record["expect"]
    problems = []
    if len(call) != len(expect):
        problems.append(
            f"scenarios/{file_name}: {len(call)} steps but {len(expect)} outcomes"
        )
    previous: str | None = None
    for index, step in enumerate(call):
        op = step["op"]
        if op == "fetch" and "spec" not in step and "ref" not in step:
            op = "fetch_answer"
        needs = _ACTS_ON_PREVIOUS.get(op)
        if needs is not None and previous not in needs:
            problems.append(
                f"scenarios/{file_name}: step {index} ({op}) does not follow "
                f"a step in {sorted(needs)}"
            )
        previous = op
    return problems


# --- refs -------------------------------------------------------------------


def _ref_rule(ref: dict[str, Any]) -> str | None:
    """The first ref rule `ref` breaks, or None."""
    seen: set[str] = set()
    for member in ref["members"]:
        spec = member["spec"]
        if spec["definition"]["spec_id"] != spec["spec_id"]:
            return "definition_spec_id_mismatch"
        if spec["spec_id"] in seen:
            return "repeated_spec_id"
        seen.add(spec["spec_id"])
    return None


def _check_refs(validator: jsonschema.Draft202012Validator) -> list[str]:
    paths = sorted((PACKAGE_DIR / "refs").glob("*.json"))
    problems = [] if paths else ["refs: no ref examples found"]
    verdicts: set[str] = set()
    for path in paths:
        label = f"refs/{path.name}"
        record = _load(path)
        if not isinstance(record, dict) or record.get("name") != path.stem:
            problems.append(f"{label}: name does not match the file name")
            continue
        error = record.get("error")
        if ("expect" in record) == isinstance(error, dict):
            problems.append(f"{label}: carries neither or both of expect and error")
            continue
        valid = validator.is_valid(record.get("ref"))
        by = error.get("by") if isinstance(error, dict) else None
        verdicts.add(by or "accept")
        if by == "schema":
            if valid:
                problems.append(f"{label}: the schema accepts a ref it should reject")
            continue
        if not valid:
            first = next(validator.iter_errors(record.get("ref")))
            problems.append(f"{label}: ref fails the schema at {_where(first)}")
            continue
        broken = _ref_rule(record["ref"])
        wanted = error.get("reason") if by == "rule" else None
        if broken != wanted:
            problems.append(f"{label}: breaks rule {broken!r}, expected {wanted!r}")
    problems.extend(
        f"refs: no {verdict} example"
        for verdict in ("accept", "schema", "rule")
        if paths and verdict not in verdicts
    )
    return problems


# --- rules ------------------------------------------------------------------


def _check_rules(validator: jsonschema.Draft202012Validator) -> list[str]:
    paths = sorted(
        p
        for p in (PACKAGE_DIR / "rules").glob("*.json")
        if p.name != "rules.schema.json"
    )
    problems = [] if paths else ["rules: no rule files found"]
    for path in paths:
        label = f"rules/{path.name}"
        document = _load(path)
        errors = _schema_problems(label, validator, document)
        problems.extend(errors)
        if errors:
            continue
        if document["rule"] != path.stem:
            problems.append(f"{label}: rule {document['rule']!r} differs")
        names = [vector["name"] for vector in document["vectors"]]
        problems.extend(
            f"{label}: vector {name!r} repeats"
            for name in sorted({n for n in names if names.count(n) > 1})
        )
    return problems


def validate() -> list[str]:
    validators = _contract_validators()
    openapi = _load(PACKAGE_DIR / "served" / "openapi.json")
    registry = _load(REGISTRY_PATH)
    problems = _check_source(_load(PACKAGE_DIR / "served" / "SOURCE.json"))

    strict = _load(PACKAGE_DIR / "strict-fields.json")
    errors = _schema_problems(
        "strict-fields.json", validators["strict-fields.schema.json"], strict
    )
    problems.extend(errors or _check_strict_fields(strict, openapi))

    actions = _load(PACKAGE_DIR / "actions.json")
    errors = _schema_problems(
        "actions.json", validators["actions.schema.json"], actions
    )
    problems.extend(errors or _check_actions(actions, registry))

    client_codes = _load(PACKAGE_DIR / "client-codes.json")
    errors = _schema_problems(
        "client-codes.json", validators["client-codes.schema.json"], client_codes
    )
    scenario_schema = validators["scenario.schema.json"].schema
    problems.extend(
        errors
        or _check_client_codes(
            client_codes, actions, registry, error_classes(scenario_schema)
        )
    )
    client_rows = [] if errors else client_codes["codes"]

    problems.extend(
        _check_scenarios(
            validators["scenario.schema.json"],
            openapi,
            registry=registry,
            strict=strict,
            actions={row["code"]: row for row in actions["codes"]},
            client_codes={row["code"]: row for row in client_rows},
        )
    )
    problems.extend(_check_refs(validators["dataset-ref.schema.json"]))
    problems.extend(_check_rules(validators["rules/rules.schema.json"]))
    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s) in {PACKAGE_DIR}", file=sys.stderr)
        return 1
    strict = _load(PACKAGE_DIR / "strict-fields.json")
    print(
        f"client contract valid: {len(_load(PACKAGE_DIR / 'actions.json')['codes'])} "
        "action rows, "
        f"{len(_load(PACKAGE_DIR / 'client-codes.json')['codes'])} client codes, "
        f"{len(strict['required'])} required and "
        f"{len(strict['enums'])} enum pointers, "
        f"{len(list((PACKAGE_DIR / 'scenarios').glob('*.json')))} scenarios"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
