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

- `served/SOURCE.json` names a 40-character dfo-apis commit, and
  `served/openapi.json` holds routes under each of SURFACE_PREFIXES and none
  outside them and PLATFORM_PREFIXES.
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
  `refusal_without_code` takes the class its status maps to, and its
  retryable flag `by_status`. Each reason the key-lookup rule raises is a
  row of class `AuthenticationError`.
- `proxy-codes.json` and `warning-codes.json` list codes in order, each once,
  none of them a code another table or `registries/codes.json` holds, and the
  warning codes are those the client rules issue: `route_deprecated`,
  `server_warning`, and one `key_<reason>` per key-format reason.
- Each scenario's file name matches its `name`, names are unique, `expect`
  holds one outcome per step, a step that acts on an earlier result follows
  one, a synthetic scenario derives from a recorded one, and every body file
  exists and is the scenario's own or, for a synthetic scenario, one its
  parent names. In a recorded scenario each API request matches a route and
  method in `served/openapi.json`, its JSON body validates against the
  route's request schema, each JSON response body validates against the
  route's schema for its status, a required member included when it admits
  null, and its `origin.dfo_commit` is `served/SOURCE.json`'s commit. In
  every scenario a problem document's `code`, a failed operation's error
  included, is an active code registered with its status, and the website
  proxy's `error` a code `proxy-codes.json` lists with its status.
- Each error a scenario expects, other than a `SearchWarning` or
  `DataWarning`, carries a refusal's code from its exchanges, a failed
  operation's included, or a client code of its class.
- Each scenario's `expect` equals the outcomes its steps reach over its
  exchanges, with refusals' error classes and gap kinds from `actions.json`
  and the class of each error the client raises itself from
  `client-codes.json`, as `expected_outcomes` derives them, the waits
  before each poll included, under the record's base URL and again under
  REPLAY_ORIGIN with the recorded origin a response names moved there; a dataset's rows, columns and per-part row
  counts are left to the Parquet bodies, and each download hashes to its
  artifact's digest or the outcome says it does not. Each Search request a
  record holds posts the input the steps derive: the question, a choice's
  data_query or dataset_resubmit entries, or a member's identity with the
  entity groups of the roles its build left bound as they were, and each
  build posts the body `build_body` gives for the spec the step builds, its
  narrowing and its layout, and each Search request the whole body
  `search_body` gives. A POST carries its body, a refused request repeated is
  a retry only as `may_retry` allows, a record that ends a run of retries
  while `may_retry` holds does not fit, each poll names the operation the
  client holds, each files route is where `artifact_route` places the
  artifact's `url`, and each signed download is where `download_target`
  places the `location` the files route answered. The outcomes give the waits
  before retries and the warnings response headers issue. Every absolute URL
  a record holds, a `url` request or a `location` header, is a signed URL on
  the storage host with the query `X-Goog-Signature=redacted`, or a
  `location` under the base URL.
- Each ref example's file name matches its `name` and gets the verdict it
  states: from `dataset-ref.schema.json`, then from the rules that each
  member's definition carries its own `spec_id` and that members hold
  distinct `spec_id`s.
- Each rule file under `rules/` validates against `rules.schema.json`, names
  the rule its file name names, and names each vector once.

It does not import `dfo_client_contract`, whose tests replay
the rule vectors. `tools/capture_client_contract.py` imports the outcome
rules from here, so the records are written by the code that checks them.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

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
        "proxy-codes.schema.json",
        "warning-codes.schema.json",
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


# The route prefixes of the surfaces the deployed composition mounts, and of
# the platform routes beside them. The served document holds these and only
# these.
SURFACE_PREFIXES = ("/api/search", "/api/datasets", "/api/graph", "/api/presentation")
PLATFORM_PREFIXES = ("/codes",)


def surface_problems(openapi: Mapping[str, Any]) -> list[str]:
    """A surface of SURFACE_PREFIXES the document lacks, and a path under no
    surface or platform prefix."""

    def under(path: str, prefix: str) -> bool:
        return path == prefix or path.startswith(prefix + "/")

    paths = list(openapi.get("paths", {}))
    problems = [
        f"served/openapi.json: no route under {prefix}, so its surface is not mounted"
        for prefix in SURFACE_PREFIXES
        if not any(under(path, prefix) for path in paths)
    ]
    problems.extend(
        f"served/openapi.json: {path} is under no deployed surface"
        for path in paths
        if not any(under(path, p) for p in (*SURFACE_PREFIXES, *PLATFORM_PREFIXES))
    )
    return problems


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
    lookup_reasons: Sequence[str],
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
    problems.extend(
        f"client-codes.json: {row['code']!r} takes its retryable flag "
        f"{BY_STATUS} exactly when it takes its class {BY_STATUS}"
        for row in document["codes"]
        if (row["retryable"] == BY_STATUS) != (row["class"] == BY_STATUS)
    )
    classes_by_code = {row["code"]: row["class"] for row in document["codes"]}
    for reason in lookup_reasons:
        found = classes_by_code.get(reason)
        if found is None:
            problems.append(
                f"client-codes.json: no row for {reason!r}, an error the "
                "key-lookup rule raises"
            )
        elif found != KEY_LOOKUP_CLASS:
            problems.append(
                f"client-codes.json: {reason!r}, an error the key-lookup rule "
                f"raises, takes {found!r}, not {KEY_LOOKUP_CLASS!r}"
            )
    return problems


def _sorted_once(label: str, codes: Sequence[str]) -> list[str]:
    problems = [
        f"{label}: code {code!r} is listed twice"
        for code in sorted({c for c in codes if codes.count(c) > 1})
    ]
    if list(codes) != sorted(codes):
        problems.append(f"{label}: codes are not sorted")
    return problems


def _check_other_codes(
    label: str, codes: Sequence[str], taken: Mapping[str, set[str]]
) -> list[str]:
    """A code table's codes are sorted, listed once, and none is a code
    another table or the registry holds."""
    problems = _sorted_once(label, codes)
    for source, others in taken.items():
        problems.extend(
            f"{label}: {code!r} is also a code in {source}"
            for code in sorted(set(codes) & others)
        )
    return problems


def _check_warning_codes(
    document: dict[str, Any],
    taken: Mapping[str, set[str]],
    key_reasons: Sequence[str],
) -> list[str]:
    """As `_check_other_codes`, and the codes are those a client's rules
    issue: each header warning, and one `key_<reason>` warning per
    key-format reason."""
    codes = [row["code"] for row in document["codes"]]
    problems = _check_other_codes("warning-codes.json", codes, taken)
    wanted = {ROUTE_DEPRECATED, SERVER_WARNING, *(f"key_{r}" for r in key_reasons)}
    problems.extend(
        f"warning-codes.json: no row for {code!r}, a warning a client issues"
        for code in sorted(wanted - set(codes))
    )
    problems.extend(
        f"warning-codes.json: {code!r} is a warning no rule issues"
        for code in sorted(set(codes) - wanted)
    )
    return problems


def key_format_reasons(rules_schema: Mapping[str, Any]) -> list[str]:
    """The reasons the key-format rule warns with."""
    vectors = rules_schema["$defs"]["key_format"]["properties"]["vectors"]
    return vectors["items"]["properties"]["reason"]["enum"]


# The class of every error the key-lookup rule raises.
KEY_LOOKUP_CLASS = "AuthenticationError"


def key_lookup_reasons(rules_schema: Mapping[str, Any]) -> list[str]:
    """The reasons the key-lookup rule raises an error with."""
    vectors = rules_schema["$defs"]["key_lookup"]["properties"]["vectors"]
    error = vectors["items"]["properties"]["error"]
    return error["properties"]["reason"]["enum"]


# --- scenarios --------------------------------------------------------------


class _Routes:
    """The served document's routes, matched against concrete paths."""

    def __init__(self, openapi: dict[str, Any]) -> None:
        self.openapi = openapi
        self._templates = [(t, t.split("/")) for t in openapi["paths"]]
        self._registry = _openapi_registry(openapi)

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
        validator = jsonschema.Draft202012Validator(
            {"$ref": f"{_OPENAPI_URI}#{pointer}"}, registry=self._registry
        )
        return [
            f"{_where(error)}: {error.message[:300]}"
            for error in validator.iter_errors(body)
        ]


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
    proxy_codes: Mapping[str, Mapping[str, Any]],
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
        problems.extend(_check_problem_code(label, response, registry, proxy_codes))
    return problems


def _check_problem_code(
    label: str,
    response: dict[str, Any],
    registry: dict[str, Any],
    proxy_codes: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """A refusal's code: a problem document's is an active registry code
    registered with its status, and the website proxy's `error` is a code
    `proxy-codes.json` lists with its status."""
    body = response.get("json")
    if response["status"] < 400 or not isinstance(body, dict):
        return []
    if "code" not in body:
        if "error" not in body:
            return []
        error = body["error"]
        row = proxy_codes.get(error) if isinstance(error, str) else None
        if row is None or row["status"] != response["status"]:
            return [
                (
                    f"{label}: proxy code {body['error']!r} is not a code "
                    f"proxy-codes.json lists with status {response['status']}"
                )
            ]
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
# The status that cuts a synchronous build off at the server's limit.
BUILD_CUT_OFF = 504


def may_retry(
    request: Mapping[str, Any], responses: Sequence[Mapping[str, Any]]
) -> bool:
    """Whether a client sends `request` again after `responses`, the answers
    to it so far in order: the last is a retryable refusal, the retries made
    stay under MAX_RETRIES, and for a build, no earlier answer was a 504
    cut-off, after which the build is posted once more only. So a build
    refused 504 then 503 raises the 503, and one refused 503 then 504 is
    posted once more after the 504."""
    *earlier, response = responses
    status = response["status"]
    if status < 400 or not retryable(status, problem_body(response)):
        return False
    if len(earlier) >= MAX_RETRIES:
        return False
    build = request["method"] == "POST" and request.get("path") == "/datasets"
    return not (build and any(r["status"] == BUILD_CUT_OFF for r in earlier))


# The content types a client reads a refusal's body as a problem document, or
# the website proxy's own body, in.
PROBLEM_MEDIA = ("application/problem+json", "application/json")


def problem_body(response: Mapping[str, Any]) -> Any:
    """A refusal's JSON body when its content type is one of PROBLEM_MEDIA,
    else None: a body in any other type reads as no body."""
    content_type = response.get("headers", {}).get("content-type", "")
    media = content_type.split(";", maxsplit=1)[0].strip().lower()
    return response.get("json") if media in PROBLEM_MEDIA else None


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


RETRY_BACKOFF_FIRST = 0.5
RETRY_BACKOFF_CAP = 8.0


def retry_wait(headers: Mapping[str, str], attempt: int) -> float:
    """The wait before retry `attempt` (from 0) of a refused request: the
    refusal's `Retry-After`, else a backoff that doubles from 0.5 to 8
    seconds."""
    wait = retry_after(headers)
    if wait is not None:
        return wait
    return min(RETRY_BACKOFF_FIRST * 2**attempt, RETRY_BACKOFF_CAP)


# The headers a client warns on, and the warning-codes.json code of each.
DEPRECATION_HEADERS = ("deprecation", "sunset")
ROUTE_DEPRECATED = "route_deprecated"
SERVER_WARNING_HEADER = "x-dfo-warning"
SERVER_WARNING = "server_warning"


def header_warnings(
    headers: Mapping[str, str], seen: set[tuple[str, ...]]
) -> list[str]:
    """The warnings an API response's headers issue, as warning-codes.json
    codes, each once per process: a `Deprecation` or `Sunset` header once per
    distinct pair of their values, either alone included, and `x-dfo-warning`
    once per message. `seen` holds the warnings the process has issued."""
    issued = []
    marks = tuple(headers.get(name, "") for name in DEPRECATION_HEADERS)
    if any(marks) and (key := (ROUTE_DEPRECATED, *marks)) not in seen:
        seen.add(key)
        issued.append(ROUTE_DEPRECATED)
    message = headers.get(SERVER_WARNING_HEADER)
    if message and (key := (SERVER_WARNING, message)) not in seen:
        seen.add(key)
        issued.append(SERVER_WARNING)
    return issued


POLL_BACKOFF_FIRST = 1.0
POLL_BACKOFF_CAP = 8.0


@dataclass
class PollPacing:
    """The wait before each poll of one long build: the `Retry-After` of the
    refusal that answered the last poll, whatever its status, else the last
    operation resource's `retry_after_ms`, else the `Retry-After` of the
    response that carried that resource, else a backoff that doubles from 1
    to 8 seconds and advances one step each time it is used, a poll after a
    refusal included."""

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

    def refused_poll(self, headers: Mapping[str, str]) -> None:
        """A refusal the client polls past answered the last poll."""
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


class _Unset:
    """The type of UNSET."""


# The trace id of an error raised without one, which `read_from` settles.
UNSET = _Unset()


class ClientError(Exception):
    """A step ends in an error a client raises. A refusal names its class,
    the server's code and its trace id, a SearchWarning or DataWarning names
    the server's warning, departure or operation code, and any other error
    names a code from `client-codes.json`, whose row gives its class. A
    VersionUnavailableError also names the newest version, or None, and the
    trace id of the refusal that gave its code. An error raised with no
    `trace_id` holds UNSET and takes its id as `read_from` gives."""

    def __init__(
        self,
        code: str | None,
        *,
        error: str | None = None,
        newest_version: str | None = None,
        trace_id: str | None | _Unset = UNSET,
    ) -> None:
        super().__init__(f"{error or 'client'} {code}")
        self.code = code
        self.error = error
        self.newest_version = newest_version
        self.trace_id = trace_id

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
        trace = None if isinstance(self.trace_id, _Unset) else self.trace_id
        if error == VERSION_UNAVAILABLE:
            return error_outcome(
                error,
                self.code,
                trace_id=trace,
                newest_version=self.newest_version,
            )
        return error_outcome(error, self.code, trace_id=trace)


TRACE_HEADER = "x-dfo-trace-id"


def trace_id(body: Any, headers: Mapping[str, str] | None) -> str | None:
    """A response's trace id: the top-level `trace_id` of `body` when it is
    an object holding one, a problem document or any other body, else its
    TRACE_HEADER header, else None. A Search answer's `diagnostics.trace_id`
    is not read."""
    found = body.get("trace_id") if isinstance(body, dict) else None
    if isinstance(found, str) and found:
        return found
    header = (headers or {}).get(TRACE_HEADER)
    return header or None


def refusal_error(
    status: int,
    body: Any,
    actions: Mapping[str, Mapping[str, Any]],
    headers: Mapping[str, str] | None = None,
) -> ClientError:
    """The error a refusal raises: its code's class and the server's code, or
    for a body with no code the class its status maps to and
    `refusal_without_code`, with the trace id `trace_id` gives for its body
    and `headers`."""
    code = refusal_code(body)
    return ClientError(
        code or REFUSAL_WITHOUT_CODE,
        error=error_class(status, code, actions),
        trace_id=trace_id(body, headers),
    )


# The codes of the errors a client raises itself that describe a response,
# beside the warnings it raises under strict; every other client code
# describes the caller's input or a storage-host fetch.
RESPONSE_ERRORS = frozenset(
    {
        "unexpected_response",
        "contract_violation",
        "answer_declined",
        "answer_incomplete",
    }
)


def read_from(error: ClientError, trace: str | None) -> ClientError:
    """`error`, raised on the API response whose trace id is `trace`: an
    error that describes a response, a RESPONSE_ERRORS code, SearchWarning
    or DataWarning, takes `trace` unless its trace id is settled; an error that
    describes the caller's input, or a storage-host fetch, keeps null."""
    if isinstance(error.trace_id, _Unset):
        reads = error.code in RESPONSE_ERRORS or error.error in WARNING_CLASSES
        error.trace_id = trace if reads else None
    return error


# The answer kinds a client reads. Any other kind raises ContractError.
ANSWER_KINDS = ("resolution", "catalogue", "declined")
RESOLUTION = "resolution"
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
    trace_id: str | None = None


def answer_from_body(body: Any) -> Answer:
    """The answer a search response holds. A body that is not an object, a
    kind no client knows, a resolution with no parts, a part whose `status`
    is not a string, and a spec whose `definition.spec_id` differs from its
    `spec_id` raise ContractError."""
    if not isinstance(body, dict) or body.get("kind") not in ANSWER_KINDS:
        raise ClientError("contract_violation")
    parts = body.get("parts", [])
    if not isinstance(parts, list) or not all(_readable(part) for part in parts):
        raise ClientError("contract_violation")
    if body["kind"] == RESOLUTION and not parts:
        raise ClientError("contract_violation")
    return Answer(body, parts, body.get("rendered_sentence"), body.get("status"))


def _readable(part: Any) -> bool:
    """Whether a part has a string `status`, and each spec it holds names
    its own spec_id in its definition."""
    if not isinstance(part, dict) or not isinstance(part.get("status"), str):
        return False
    specs = [part.get("spec")] if part["status"] == "definite" else []
    if part["status"] == "candidates":
        specs = (part.get("candidates") or {}).get("specs") or []
    return all(
        not isinstance(spec, dict)
        or not isinstance(spec.get("definition"), dict)
        or spec["definition"].get("spec_id") == spec.get("spec_id")
        for spec in specs
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


def choice_input(answer: Answer, choice: Choice) -> dict[str, Any]:
    """The Search input a choice posts. In a one-part answer an option posts
    its data_query; any other choice posts dataset_resubmit with one entry
    per part, in part order, so a picked candidate in a one-part answer posts
    its identity as the one entry, and Search answers the picked reading."""
    if len(answer.parts) == 1 and choice.option is not None:
        _option_part(choice.option)
        return _data_query(choice.option)
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
    its restatement as a parsed entry, or a placeholder when it has none.
    Each identity, the chosen candidate's, a definite part's and each of a
    candidates entry's, echoes its reading's assumptions as `reading_echo`
    gives them."""
    if choice is not None and choice.candidate is not None:
        candidate = choice.candidate
        return _echo(
            part,
            entry_kind="identity",
            **_identity(candidate, reading_echo(part, candidate)),
        )
    if choice is not None and choice.option is not None:
        return {"entry_kind": "parsed", "part": _option_part(choice.option)}
    match part.get("status"):
        case "definite":
            spec = part["spec"]
            return _echo(
                part, entry_kind="identity", **_identity(spec, reading_echo(part, spec))
            )
        case "candidates":
            candidates = part["candidates"]
            return _echo(
                part,
                entry_kind="candidates",
                ordering=candidates.get("ordering"),
                truncated=candidates.get("truncated") is True,
                candidates=[
                    _identity(spec, reading_echo(part, spec))
                    for spec in candidates["specs"]
                ],
            )
    return _restated_entry(part)


def _data_query(option: Mapping[str, Any]) -> dict[str, Any]:
    """A no_data option's `data_query`; an option without one raises
    ContractError, in a one-part answer as in a several-part one."""
    query = option.get("data_query")
    if not isinstance(query, dict):
        raise ClientError("contract_violation")
    return query


def _option_part(option: Mapping[str, Any]) -> Any:
    """The one part a no_data option's `data_query` holds; a query of other
    than one part raises ContractError, in a one-part answer as in a
    several-part one."""
    parts = _data_query(option).get("parts")
    if not isinstance(parts, list) or len(parts) != 1:
        raise ClientError("contract_violation")
    return parts[0]


def reading_echo(part: Mapping[str, Any], spec: Mapping[str, Any]) -> Any:
    """The assumptions a resubmitted reading of `part` echoes: a definite
    part's `assumptions`, or a candidates part's `reading_assumptions` under
    the reading's spec_id. None, when the part carries no such member, posts
    no `assumptions`, and Search reads the reading as a fresh request does;
    `[]` states none."""
    if part.get("status") == "candidates":
        return (part.get("reading_assumptions") or {}).get(spec["spec_id"])
    return part.get("assumptions")


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


def _identity(spec: dict[str, Any], assumptions: Any = None) -> dict[str, Any]:
    """A spec's identity, its entity groups when it has any, and the
    reading's `assumptions` echo unless it is None."""
    item: dict[str, Any] = {"identity": spec["definition"]["identity"]}
    groups = spec.get("resolved", {}).get("entity_groups")
    if groups:
        item["entity_groups"] = groups
    if assumptions is not None:
        item["assumptions"] = assumptions
    return item


def listed(holder: Mapping[str, Any], name: str) -> list[Any]:
    """A member a client reads as a list, and that no strict pointer names:
    a member of another type reads as absent, as an empty list."""
    value = holder.get(name)
    return value if isinstance(value, list) else []


def _echo(part: dict[str, Any], **members: Any) -> dict[str, Any]:
    """An entry with the members every entry but a parsed one echoes from its
    part: variable_text, separate when true, and operations when any."""
    if not part.get("variable_text"):
        raise _refuse()
    entry: dict[str, Any] = {**members, "variable_text": part["variable_text"]}
    if part.get("separate") is True:
        entry["separate"] = True
    if operations := listed(part, "operations"):
        entry["operations"] = operations
    return entry


def reading_hints(answer: Answer) -> dict[str, str]:
    """How `answer`'s readings differ from the question, each line a client
    shows once, with its code. In part order, each definite part gives each
    operation Search did not run, coded by its verb, each departure, and each
    warning shown at `warn` or `degraded` severity; the answer's diagnostics
    warnings follow. An `info` warning gives nothing."""
    hints: dict[str, str] = {}

    def notices(warnings: list[Any]) -> None:
        for warning in warnings:
            if warning.get("severity") in SHOWN_SEVERITIES and warning.get("message"):
                hints.setdefault(f"Note: {warning['message']}", warning["code"])

    for part in answer.parts:
        if part.get("status") != "definite":
            continue
        for operation in listed(part, "operations"):
            line = f"Not done: {operation['verb']} ({json.dumps(operation['phrase'])})"
            hints.setdefault(line, operation["verb"])
        for departure in listed(part, "departures"):
            hints.setdefault(departure["text"], departure["code"])
        notices(listed(part, "warnings"))
    notices(listed(answer.body.get("diagnostics", {}), "warnings"))
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
    the variable text of every part it answers that names one, and the first
    such part's spec."""
    members: dict[str, tuple[list[str], dict[str, Any]]] = {}
    for part in answer.parts:
        if part.get("status") != "definite":
            continue
        spec = part["spec"]
        named = members.setdefault(spec["spec_id"], ([], spec))[0]
        if part.get("variable_text"):
            named.append(part["variable_text"])
    return list(members.values())


def held_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """A served spec as a client holds it, the shape of a step's `spec`: its
    spec_id, definition and sentence, and the entity groups its `resolved`
    names, when it names any."""
    resolved = spec["resolved"]
    held = {
        "spec_id": spec["spec_id"],
        "definition": spec["definition"],
        "sentence": resolved["rendered_sentence"],
    }
    if groups := resolved.get("entity_groups"):
        held["entity_groups"] = groups
    return held


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


def json_type(value: Any) -> str:
    """The JSON type of a parsed value, an integer's as `integer`."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    for kind, name in ((dict, "object"), (list, "array"), (str, "string")):
        if isinstance(value, kind):
            return name
    return "number" if isinstance(value, float) else "null"


def schema_types(openapi: dict[str, Any], schema: Any) -> set[str] | None:
    """The JSON types `schema` admits, through `$ref`, `anyOf`, `oneOf` and
    `allOf`, with `number` admitting an integer; None when it names none."""
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        return schema_types(openapi, _resolve(openapi, ref[1:]))
    if "type" in schema:
        named = schema["type"]
        types = set(named) if isinstance(named, list) else {named}
        return types | {"integer"} if "number" in types else types
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            branches = [schema_types(openapi, b) for b in schema[keyword]]
            if any(b is None for b in branches):
                return None
            return set().union(*(b for b in branches if b is not None))
    for branch in schema.get("allOf", []):
        if (types := schema_types(openapi, branch)) is not None:
            return types
    return {"object"} if "properties" in schema else None


def strict_gaps(
    openapi: dict[str, Any],
    members: Mapping[str, Sequence[str]],
    schema: Any,
    body: Any,
    where: str = "",
    component: str | None = None,
) -> list[str]:
    """The strict members `body` lacks, holds as null, or holds as a JSON
    type their schema does not admit, read through `schema`: each component
    schema it reaches names its own, and a
    discriminated union whose discriminator `body` lacks is a gap. The walk
    goes down only through strict members, the ones a client reads, so a
    member no client reads, such as a spec's `resolved.equivalent_readings`,
    is never checked. A discriminator value the union does not map, and a
    union without a discriminator that offers more than one object branch,
    reach nothing. `component` is the component schema `schema` belongs to."""
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        name = ref.rsplit("/", 1)[-1]
        gaps = []
        if isinstance(body, dict):
            gaps = [
                f"{where}/{member}"
                for member in members.get(name, ())
                if body.get(member) is None
            ]
        return gaps + strict_gaps(
            openapi, members, _resolve(openapi, ref[1:]), body, where, name
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
            gaps.extend(
                strict_gaps(openapi, members, branches[0], body, where, component)
            )
    for branch in schema.get("allOf", []):
        gaps.extend(strict_gaps(openapi, members, branch, body, where, component))
    if isinstance(body, dict):
        read = members.get(component, ()) if component is not None else ()
        for name, member in schema.get("properties", {}).items():
            if name not in body or name not in read or body[name] is None:
                continue
            types = schema_types(openapi, member)
            if types is not None and json_type(body[name]) not in types:
                gaps.append(f"{where}/{name}")
                continue
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
# The path a client polls an operation at, under the base URL.
POLL_PATH = "/datasets/operations/{operation_id}"
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
NO_VERSIONS = frozenset({"NotFoundError"})
OPERATION_FAILED = "operation_failed"


def rendering_for(layout: str | None) -> dict[str, Any]:
    """The build request's `options` for a fetch's `layout`, long by default."""
    return dict(LAYOUT_RENDERINGS[layout or "long"])


def posted_scope(step: Mapping[str, Any]) -> dict[str, Any] | None:
    """The `input.scope` a fetch of a spec posts for the step's
    narrowing, or None when it narrows nothing. The server's
    `scope.entities` replaces the spec's whole entity binding, so the
    client posts the spec's bound roles with each role the step names
    replaced, and a role named with null dropped. A step that names no
    role, or no facet, posts no `entities` or `facets`, so `{}` narrows
    nothing."""
    scope: dict[str, Any] = {}
    window = step.get("window")
    if isinstance(window, list):
        start, end = window
        scope["window"] = {"kind": "range", "start_year": start, "end_year": end}
    elif window is not None:
        scope["window"] = {"kind": window}
    if named := step.get("entities"):
        bound = dict(step["spec"]["definition"]["identity"].get("entities", {}))
        for role, refs in named.items():
            if refs is None:
                bound.pop(role, None)
            else:
                bound[role] = list(refs)
        scope["entities"] = bound
    if facets := step.get("facets"):
        scope["facets"] = {name: {"ref": ref} for name, ref in facets.items()}
    return scope or None


def versions_path(spec_id: str) -> str:
    """The versions route asked for the newest stored version alone: the
    route lists versions newest first, so `limit=1` returns just it."""
    return f"/datasets/{spec_id}/versions?limit=1"


def search_body(input_: Mapping[str, Any]) -> dict[str, Any]:
    """The whole body a client posts to Search for `input_`."""
    return {"schema_version": 1, "input": input_}


def newest_listed(body: Any) -> str | None:
    """The newest version a versions page lists, its first, or None when it
    lists none. A body whose `versions` is not a list of versions, each with
    a `content_version`, raises ContractError."""
    versions = body.get("versions") if isinstance(body, dict) else None
    if not isinstance(versions, list) or not all(
        isinstance(v, dict) and isinstance(v.get("content_version"), str)
        for v in versions
    ):
        raise ClientError("contract_violation")
    return versions[0]["content_version"] if versions else None


def build_body(
    spec: Mapping[str, Any],
    scope: Mapping[str, Any] | None,
    rendering: Mapping[str, Any],
) -> dict[str, Any]:
    """The body a client posts to build `spec`, a step's `spec` or a ref's:
    its spec_id and unmodified definition, `scope` when it narrows anything,
    and `rendering` as the options."""
    input_: dict[str, Any] = {
        "kind": "spec_id",
        "spec_id": spec["spec_id"],
        "definition": spec["definition"],
    }
    if scope:
        input_["scope"] = dict(scope)
    return {"schema_version": 1, "input": input_, "options": dict(rendering)}


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


# The storage host a signed download names, the only host outside the API a
# client fetches from, and the query every signed URL in a record carries.
STORAGE_ORIGIN = "https://storage.googleapis.com"
REDACTED_QUERY = "X-Goog-Signature=redacted"
# The successes a files route answers with the file's `location`; any other
# 2xx raises ContractError `unexpected_response`. A signed fetch that answers
# other than SIGNED_FETCH_STATUS is a failed fetch.
FILES_ROUTE_STATUSES = frozenset({200, 302})
SIGNED_FETCH_STATUS = 200


def files_route_unexpected(status: int) -> bool:
    """Whether a files route's answer is a 2xx other than those of
    FILES_ROUTE_STATUSES, which raises ContractError `unexpected_response`."""
    return 200 <= status < 300 and status not in FILES_ROUTE_STATUSES


def download_target(base_url: str, route_url: str, location: str) -> tuple[str, str]:
    """Where a client fetches the file a files route's `location` names,
    as `(kind, target)`: a URL on the storage host as `url`, fetched bare;
    or a URL on the API's origin, a relative one resolved against
    `route_url`, as the `origin_path` it reaches. A URL on any other origin
    raises ContractError."""
    resolved = urlsplit(urljoin(route_url, location))
    origin = f"{resolved.scheme}://{resolved.netloc}"
    if origin == STORAGE_ORIGIN:
        return "url", location
    base = urlsplit(base_url)
    if origin != f"{base.scheme}://{base.netloc}":
        raise ClientError("contract_violation")
    return "origin_path", urlunsplit(("", "", resolved.path, resolved.query, ""))


def artifact_route(base_url: str, url: str) -> str:
    """The `origin_path` of the files route a manifest artifact's `url`
    names: a root-relative url as it resolves on the API's origin, and an
    absolute url on that origin as its path and query. A url on any other
    origin, the storage host's included, raises ContractError."""
    base = urlsplit(base_url)
    origin = f"{base.scheme}://{base.netloc}"
    resolved = urlsplit(urljoin(f"{origin}/", url))
    if f"{resolved.scheme}://{resolved.netloc}" != origin:
        raise ClientError("contract_violation")
    return urlunsplit(("", "", resolved.path, resolved.query, ""))


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


def kept_entity_groups(
    held: Mapping[str, Any] | None, built: Mapping[str, Any]
) -> dict[str, Any]:
    """The held spec's entity groups a built definition keeps: the group of
    each role the built identity binds to the same entities the held
    definition binds it to. A role the narrowing rebound or dropped loses its
    group; a window or facets narrowing keeps every group."""
    if held is None or not (groups := held.get("entity_groups")):
        return {}
    before = held["definition"]["identity"].get("entities", {})
    after = built["identity"].get("entities", {})
    return {
        role: group
        for role, group in groups.items()
        if role in after and set(after[role]) == set(before.get(role, ()))
    }


def sentence_entry(
    parts: list[str], built: Mapping[str, Any], held: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The dataset_resubmit identity entry that asks Search for a built
    definition's sentence: the built identity, the member's first part or
    UNNAMED_VARIABLE_TEXT, and the entity groups `kept_entity_groups` gives,
    left out when it gives none. `held` is the spec the client built from,
    None for a resumed operation."""
    entry: dict[str, Any] = {
        "entry_kind": "identity",
        "variable_text": parts[0] if parts else UNNAMED_VARIABLE_TEXT,
        "identity": built["identity"],
    }
    if groups := kept_entity_groups(held, built):
        entry["entity_groups"] = groups
    return entry


def failed_operation(
    resource: Mapping[str, Any],
    actions: Mapping[str, Mapping[str, Any]],
    headers: Mapping[str, str] | None = None,
) -> tuple[ClientError, bool]:
    """A failed operation's error, the refusal its problem document states at
    that document's status or else 500, and whether it is retryable. A
    failed operation with no document raises `operation_failed`. Its trace id
    is the document's, else the `headers` of the response that carried the
    resource."""
    problem = resource.get("error")
    if not isinstance(problem, dict):
        error = ClientError(
            OPERATION_FAILED,
            error=error_class(500, OPERATION_FAILED, actions),
            trace_id=trace_id(None, headers),
        )
        return error, retryable(500, None)
    status = problem.get("status")
    status = status if isinstance(status, int) else 500
    error = refusal_error(status, problem, actions, headers)
    return error, retryable(status, problem)


class LongBuild:
    """A build the server runs on, followed the way a client polls it. Each
    wait before a poll is `PollPacing`'s. A 429 poll spends no retry, any
    other retryable refused poll one of MAX_RETRIES, and any other refused
    poll raises; the poll after a refusal waits as `PollPacing` gives. A poll answering a failed operation, retryable by its
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
        self.headers = headers
        self.pacing.took(resource["operation"], headers)
        if resource["operation"]["status"] == "done":
            manifest = resource.get("manifest")
            if not isinstance(manifest, dict):
                raise ClientError("contract_violation")
            self.result = manifest

    def next_wait(self) -> float:
        """The wait before the next poll, kept in `entry`."""
        if self.resource["operation"]["status"] == "failed":
            raise failed_operation(self.resource, self.actions, self.headers)[0]
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
            error, again = failed_operation(body, self.actions, headers)
        else:
            error = refusal_error(status, body, self.actions, headers)
            again = retryable(status, body)
            if error.code != OPERATION_FAILED:
                if status != 429:
                    if not again or self.retries >= MAX_RETRIES:
                        raise error
                    self.retries += 1
                self.pacing.refused_poll(headers)
                return False
        if not again or self.request is None or self.entry["resubmissions"]:
            raise error
        self.entry["resubmissions"] += 1
        return True

    def reposted(self, status: int, headers: Mapping[str, str], body: Any) -> None:
        """Takes the answer to the build posted again: a refusal raises, a
        202 starts the operation the client polls next, and a 200 is done."""
        if status >= 400:
            raise refusal_error(status, body, self.actions, headers)
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
    base_url: str
    members: Mapping[str, Sequence[str]]
    actions: Mapping[str, Mapping[str, Any]]
    client_codes: Mapping[str, Mapping[str, Any]]
    body_dir: Path
    cursor: int = 0
    answer: Answer | None = None
    minted: _Minted | None = None
    long_builds: list[dict[str, Any]] = field(default_factory=list)
    retry_waits: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    warned: set[tuple[str, ...]] = field(default_factory=set)
    read_trace: str | None = None

    @property
    def kinds(self) -> dict[str, str]:
        return gap_kinds(self.actions)

    @property
    def base_path(self) -> str:
        return urlsplit(self.base_url).path.rstrip("/")

    # -- exchanges --

    def call(
        self, method: str, target: str, *, kind: str = "path", retries: bool = True
    ) -> dict[str, Any]:
        """The exchange a client acts on for its next request, whose `kind`,
        `path`, `origin_path` or `url`, is exactly `target`. With `retries`,
        a refusal followed by the same request is retried as `may_retry`
        allows, each retry after the wait `retry_wait` gives, and the last
        exchange of the run is the one acted on; a run that ends while
        `may_retry` still holds lacks a retry the client makes. Each API
        response issues the warnings its headers carry."""
        if self.cursor >= len(self.exchanges):
            raise _UnfitError(f"the steps make a {method} {target} the record lacks")
        exchange = self.exchanges[self.cursor]
        request = exchange["request"]
        if request["method"] != method or request.get(kind) != target:
            actual = request.get("path") or request.get("origin_path") or request["url"]
            raise _UnfitError(
                f"exchange {self.cursor} is {request['method']} {actual}, where the "
                f"steps make a {method} {target}"
            )
        self.cursor += 1
        self.took(exchange)
        answers = [exchange["response"]]
        while (
            retries
            and self.cursor < len(self.exchanges)
            and self.exchanges[self.cursor]["request"] == request
            and may_retry(request, answers)
        ):
            headers = exchange["response"].get("headers", {})
            self.retry_waits.append(retry_wait(headers, len(answers) - 1))
            exchange = self.exchanges[self.cursor]
            self.cursor += 1
            self.took(exchange)
            answers.append(exchange["response"])
        if retries and may_retry(request, answers):
            raise _UnfitError(
                f"exchange {self.cursor - 1} is a retryable refusal with retries "
                f"left, and the record holds no retry of {method} {target}"
            )
        return exchange

    def took(self, exchange: dict[str, Any]) -> None:
        """Notes the warnings an API response's headers issue, and its trace
        id as the one an error raised on reading it carries."""
        if "url" not in exchange["request"]:
            response = exchange["response"]
            headers = response.get("headers", {})
            self.warnings.extend(header_warnings(headers, self.warned))
            self.read_trace = trace_id(response.get("json"), headers)

    def acted_on(self, exchange: dict[str, Any], schema: Any = None) -> Any:
        """The JSON body a client acts on, read through `schema` or else its
        route's schema for its status. A refusal raises its error, a success
        of a JSON route without a JSON body raises ContractError
        `unexpected_response`, and a body without a strict member raises
        ContractError."""
        response = exchange["response"]
        body = response.get("json")
        if response["status"] >= 400:
            raise refusal_error(
                response["status"],
                problem_body(response),
                self.actions,
                response.get("headers"),
            )
        if schema is None:
            schema = self.routes.response_schema(
                self.base_path, exchange["request"], response["status"]
            )
        if schema is not None and "json" not in response:
            raise ClientError("unexpected_response")
        self.strict(schema, body)
        return body

    def answered(self, response: Mapping[str, Any], schema: Any) -> Any:
        """The body of a response the long-build rules read: a refusal's
        problem body, or a success's JSON body read through `schema`, which a
        success without one lacks, raising ContractError
        `unexpected_response`."""
        if response["status"] >= 400:
            return problem_body(response)
        if "json" not in response:
            raise ClientError("unexpected_response")
        self.strict(schema, response["json"])
        return response["json"]

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
        `origin_path`, as `artifact_route` places an artifact's `url`, when
        `origin`, else a path under the base URL. A route answering a 2xx
        other than 200 or 302 raises ContractError `unexpected_response`.
        The client fetches once the URL the route's `location` names, as
        `download_target` places it. A fetch answering other than 200, or
        bytes that do not hash to `digest`, ask the route once more, and a
        second failure raises."""
        failure = ClientError("download_failed")
        base = urlsplit(self.base_url)
        route_url = (
            f"{base.scheme}://{base.netloc}{target}"
            if origin
            else f"{self.base_url.rstrip('/')}{target}"
        )
        for _ in range(2):
            kind = "origin_path" if origin else "path"
            exchange = self.call("GET", target, kind=kind)
            self.acted_on(exchange)
            if files_route_unexpected(exchange["response"]["status"]):
                raise ClientError("unexpected_response")
            location = exchange["response"].get("headers", {}).get("location")
            if not isinstance(location, str):
                raise _UnfitError(f"the redirect from {target} names no location")
            fetched, at = download_target(self.base_url, route_url, location)
            signed = self.call("GET", at, kind=fetched, retries=False)["response"]
            if signed["status"] != SIGNED_FETCH_STATUS:
                failure = ClientError("download_failed")
                continue
            if digest_of(self.body(signed)) == digest:
                return
            failure = ClientError("digest_mismatch")
        raise failure

    def resolve(self, posts: dict[str, Any]) -> Answer:
        """A Search request whose recorded body is the one `search_body` gives
        for the input `posts`."""
        exchange = self.posted(_SEARCH, search_body(posts))
        answer = answer_from_body(self.acted_on(exchange))
        return replace(answer, trace_id=self.read_trace)

    def posted(self, route: tuple[str, str], posts: dict[str, Any]) -> dict[str, Any]:
        """A request to `route` whose recorded body is `posts`, whole."""
        exchange = self.call(*route)
        sent = exchange["request"].get("json")
        if sent != posts:
            raise _UnfitError(
                f"exchange {self.cursor - 1} posts {json.dumps(sent)[:300]}, "
                f"where the rules post {json.dumps(posts)[:300]}"
            )
        return exchange

    # -- builds --

    def posted_build(
        self,
        spec: Mapping[str, Any],
        rendering: dict[str, Any],
        scope: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """A build request whose recorded body is the one `build_body` gives
        for `spec`, `scope` and `rendering`."""
        return self.posted(_BUILD, build_body(spec, scope, rendering))

    def build(
        self,
        spec: Mapping[str, Any],
        rendering: dict[str, Any],
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A build of `spec`'s latest version, posting `rendering` as its
        options and `scope` as its input's scope: the manifest it answers,
        through a long build when it answers 202."""
        exchange = self.posted_build(spec, rendering, scope)
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
            path = POLL_PATH.format(operation_id=build.entry["operation_id"])
            response = self.call("GET", path, retries=False)["response"]
            body = self.answered(response, _OPERATION)
            if not build.polled(response["status"], response.get("headers", {}), body):
                continue
            again = self.call(*_BUILD)
            if again["request"] != request:
                raise _UnfitError("the build posted again posts another request")
            reply = again["response"]
            schema = _OPERATION if reply["status"] == 202 else _MANIFEST
            body = self.answered(reply, schema)
            build.reposted(reply["status"], reply.get("headers", {}), body)
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
        return self.member(parts, spec, self.build(spec, rendering), rendering, strict)

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
        route = artifact_route(self.base_url, artifact["url"])
        built = manifest["definition"]["spec_id"]
        sentence = None if spec is None else spec.get("sentence")
        if spec is None or built != spec["spec_id"] or sentence is None:
            sentence = self.sentence(parts, manifest["definition"], spec)
        self.download(route, artifact["artifact_digest"], origin=True)
        return {
            "parts": parts,
            "spec_id": built,
            "content_version": manifest["content_version"],
            "sentence": sentence,
        }

    def sentence(
        self,
        parts: list[str],
        definition: dict[str, Any],
        held: dict[str, Any] | None,
    ) -> str:
        """Search's sentence for a built identity, posted with the entity
        groups of `held` that the build keeps. An answer that is not one
        definite part of the built spec_id, with a sentence, raises
        ContractError."""
        entry = sentence_entry(parts, definition, held)
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
            manifest = self.build(spec, rendering)
        except ClientError as error:
            if error.error_class(self.client_codes) not in REBUILD_REFUSED:
                raise
            refusal = refusal if refusal is not None else error
            newest = self.newest(spec_id)
        else:
            if manifest["content_version"] == version:
                held = {**spec, "sentence": member["sentence"]}
                return self.member(member["parts"], held, manifest, rendering, False)
            newest = manifest["content_version"]
        raise ClientError(
            refusal.code if refusal is not None else "version_unavailable",
            error=VERSION_UNAVAILABLE,
            newest_version=newest,
            trace_id=refusal.trace_id if refusal is not None else None,
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
                route = artifact_route(self.base_url, artifact["url"])
                self.download(route, digest, origin=True)
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
        """The newest stored version, the one the versions route lists first,
        or None when it lists none or refuses the spec as unknown; a page
        that lists no versions in the form `newest_listed` reads raises."""
        try:
            body = self.acted_on(self.call("GET", versions_path(spec_id)))
        except ClientError as error:
            if error.error_class(self.client_codes) in NO_VERSIONS:
                return None
            raise
        return newest_listed(body)

    # -- steps --

    def step(self, step: dict[str, Any]) -> dict[str, Any]:
        """The outcome `step` reaches. Whatever the outcome, a step that made
        a long build names each in `long_builds`, a step that retried a
        refused request gives the waits before its retries in
        `retry_waits`, and a step whose responses issued warnings gives their
        codes in `warnings`."""
        self.long_builds, self.retry_waits, self.warnings = [], [], []
        self.read_trace = None
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
            outcome = read_from(ended, self.read_trace).outcome(self.client_codes)
        return with_step_notes(
            outcome,
            long_builds=self.long_builds,
            retry_waits=self.retry_waits,
            warnings=self.warnings,
        )

    def _answered(self) -> Answer:
        if self.answer is None:
            raise _UnfitError("the step follows no answer")
        return self.answer

    def _search(self, step: dict[str, Any]) -> dict[str, Any]:
        self.answer = self.resolve({"kind": "raw_text", "query": step["question"]})
        return answer_outcome(self.answer, self.kinds)

    def _choose(self, step: dict[str, Any]) -> dict[str, Any]:
        answer = self._answered()
        try:
            choice = choice_for(answer, self.kinds, step.get("part"), step["option"])
            posts = choice_input(answer, choice)
        except ClientError as error:
            raise read_from(error, answer.trace_id) from None
        chosen = self.resolve(posts)
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
            raise read_from(ClientError("answer_declined"), answer.trace_id)
        gaps = answer_gaps(answer, self.kinds)
        readings = answer_members(answer)
        if gaps and (not step.get("partial", False) or not readings):
            raise read_from(ClientError("answer_incomplete"), answer.trace_id)
        warns = search_warns(answer)
        strict = step.get("strict", False)
        if warns and strict:
            raise read_from(
                ClientError(search_warning_code(answer), error="SearchWarning"),
                answer.trace_id,
            )
        rendering = rendering_for(step.get("layout"))
        if "layout" in rendering and len(readings) > 1:
            raise ClientError("invalid_argument")
        members = [
            self.fetched(
                parts,
                held_spec(spec),
                rendering,
                strict,
            )
            for parts, spec in readings
        ]
        return {"dataset": dataset_summary(members, gaps, search_warning=warns)}

    def _fetch_spec(self, step: dict[str, Any]) -> dict[str, Any]:
        rendering = rendering_for(step.get("layout"))
        manifest = self.build(step["spec"], rendering, posted_scope(step))
        strict = step.get("strict", False)
        member = self.member([], step["spec"], manifest, rendering, strict)
        return {"dataset": dataset_summary([member], [])}

    def _fetch_ref(self, step: dict[str, Any]) -> dict[str, Any]:
        members = [self.stored_version(member) for member in step["ref"]["members"]]
        return {"dataset": dataset_summary(members, [])}

    def _mint(self, step: dict[str, Any]) -> dict[str, Any]:
        rendering = rendering_for(step.get("layout"))
        exchange = self.posted_build(step["spec"], rendering, None)
        if exchange["response"]["status"] == 200:
            raise _UnfitError(
                "a mint step's build answers 200; a mint expects an operation"
            )
        resource = self.acted_on(exchange, _OPERATION)
        headers = exchange["response"].get("headers", {})
        self.minted = _Minted(
            resource, headers, exchange["request"], step["spec"], rendering
        )
        return _operation_outcome(resource)

    def _operation(self, step: dict[str, Any]) -> dict[str, Any]:
        exchange = self.call("GET", POLL_PATH.format(operation_id=step["operation_id"]))
        resource = self.acted_on(exchange, _OPERATION)
        headers = exchange["response"].get("headers", {})
        rendering = rendering_for(step.get("layout"))
        self.minted = _Minted(resource, headers, None, None, rendering)
        return _operation_outcome(resource)

    def _newest_version(self, step: dict[str, Any]) -> dict[str, Any]:
        return {
            "newest_version": {"content_version": self.newest(step["spec"]["spec_id"])}
        }

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


def with_step_notes(
    outcome: dict[str, Any],
    *,
    long_builds: Sequence[dict[str, Any]],
    retry_waits: Sequence[float],
    warnings: Sequence[str],
) -> dict[str, Any]:
    """`outcome` with the step's `long_builds`, `retry_waits` and `warnings`,
    each given only when it holds any."""
    (value,) = outcome.values()
    for name, notes in (
        ("long_builds", long_builds),
        ("retry_waits", retry_waits),
        ("warnings", warnings),
    ):
        if notes:
            value[name] = list(notes)
    return outcome


def expected_outcomes(
    record: dict[str, Any],
    *,
    routes: _Routes,
    base_url: str,
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
        base_url,
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


# The origin of the second base URL the validator replays each scenario
# under, on a host no record names.
REPLAY_ORIGIN = "https://replay.example.org"


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def relocated(
    exchanges: Sequence[Mapping[str, Any]], recorded: str, origin: str
) -> list[dict[str, Any]]:
    """`exchanges` as a replay under a base URL on `origin` serves them: the
    `recorded` origin, the one the record's `origin.base_url` names, becomes
    `origin` wherever a response names it, in its JSON body and its
    headers."""

    def moved(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(recorded, origin)
        if isinstance(value, list):
            return [moved(item) for item in value]
        if isinstance(value, dict):
            return {key: moved(item) for key, item in value.items()}
        return value

    return [
        {"request": exchange["request"], "response": moved(exchange["response"])}
        for exchange in exchanges
    ]


def owns_body_file(name: str, body_file: str) -> bool:
    """Whether `body_file` is one the scenario `name` writes: `<name>.<ext>`,
    or `<name>-<n>.<ext>` for its n-th body."""
    return (
        re.fullmatch(rf"{re.escape(name)}(-[0-9]+)?\.[a-z0-9]+", body_file) is not None
    )


def _under(url: str, base_url: str) -> bool:
    """Whether `url` names a route under `base_url`: the same scheme, host
    and port, and a path under the base URL's path."""
    parts, base = urlsplit(url), urlsplit(base_url)
    root = base.path.rstrip("/")
    return (parts.scheme, parts.hostname, parts.port) == (
        base.scheme,
        base.hostname,
        base.port,
    ) and (parts.path == root or parts.path.startswith(root + "/"))


def absolute_url_problems(record: Mapping[str, Any], base_url: str) -> list[str]:
    """Each absolute URL a record holds, a `url` request or a `location`
    header, that is not a signed URL on the storage host, over https and
    with the redacted query; a `location` may also name a route under
    `base_url`. A local app's host, or any other, is refused by name."""
    storage = urlsplit(STORAGE_ORIGIN)
    problems = []
    for index, exchange in enumerate(record["exchanges"]):
        label = f"scenarios/{record['name']}.json: exchange {index}"
        location = exchange["response"].get("headers", {}).get("location")
        for url, api in ((exchange["request"].get("url"), False), (location, True)):
            if not isinstance(url, str) or not urlsplit(url).netloc:
                continue
            parts = urlsplit(url)
            if api and _under(url, base_url):
                continue
            if (parts.scheme, parts.netloc) != (storage.scheme, storage.netloc):
                problems.append(
                    f"{label}: {url.split('?', 1)[0]} names {parts.netloc}, where "
                    f"a record's absolute URL names {STORAGE_ORIGIN}"
                )
            elif parts.query != REDACTED_QUERY:
                problems.append(
                    f"{label}: {url.split('?', 1)[0]} keeps a query other than "
                    f"{REDACTED_QUERY}"
                )
    return problems


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
    proxy_codes: Mapping[str, Mapping[str, Any]],
    served_commit: str | None,
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
        base_url = recorded["origin"]["base_url"]
        base_path = urlsplit(base_url).path.rstrip("/")
        if origin["kind"] == "recorded" and origin["dfo_commit"] != served_commit:
            problems.append(
                f"scenarios/{file_name}: dfo_commit {origin['dfo_commit']} is not "
                f"served/SOURCE.json's commit {served_commit}"
            )
        problems.extend(absolute_url_problems(record, base_url))
        for index, exchange in enumerate(record["exchanges"]):
            label = f"scenarios/{file_name}: exchange {index}"
            if origin["kind"] == "recorded":
                problems.extend(
                    _check_exchange(
                        label, exchange, base_path, routes, registry, proxy_codes
                    )
                )
            else:
                problems.extend(
                    _check_problem_code(
                        label, exchange["response"], registry, proxy_codes
                    )
                )
        for resource in _failed_operations(record):
            error = resource.get("error")
            status = error.get("status") if isinstance(error, dict) else None
            problems.extend(
                _check_problem_code(
                    f"scenarios/{file_name}: failed operation",
                    {
                        "status": status if isinstance(status, int) else 500,
                        "json": error,
                    },
                    registry,
                    proxy_codes,
                )
            )
        replay = partial(
            expected_outcomes,
            routes=routes,
            strict=strict,
            actions=actions,
            client_codes=client_codes,
            body_dir=directory,
        )
        try:
            derived = replay(record, base_url=base_url)
        except ValueError as exc:
            problems.append(f"scenarios/{file_name}: {exc}")
            continue
        problems.extend(_check_expect(file_name, record, derived))
        elsewhere = REPLAY_ORIGIN + urlsplit(base_url).path
        exchanges = relocated(record["exchanges"], origin_of(base_url), REPLAY_ORIGIN)
        try:
            moved = replay({**record, "exchanges": exchanges}, base_url=elsewhere)
        except ValueError as exc:
            moved = [{"unfit": str(exc)}]
        if moved != derived:
            problems.append(
                f"scenarios/{file_name}: replayed under {elsewhere}, with the "
                f"recorded origin replaced by its own, it gives "
                f"{json.dumps(moved)[:300]}, not the outcomes it gives under "
                f"{base_url}"
            )

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
    } | {
        refusal_code(body["error"])
        if isinstance(body.get("error"), dict)
        # A failed operation with no document raises operation_failed.
        else OPERATION_FAILED
        for body in _failed_operations(record)
    }
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
    source = _load(PACKAGE_DIR / "served" / "SOURCE.json")
    problems = _check_source(source)
    problems.extend(surface_problems(openapi))

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
    rules_schema = validators["rules/rules.schema.json"].schema
    problems.extend(
        errors
        or _check_client_codes(
            client_codes,
            actions,
            registry,
            error_classes(scenario_schema),
            key_lookup_reasons(rules_schema),
        )
    )
    client_rows = [] if errors else client_codes["codes"]

    taken = {
        "registries/codes.json": {entry["code"] for entry in registry["entries"]},
        "actions.json": {row["code"] for row in actions["codes"]},
        "client-codes.json": {row["code"] for row in client_rows},
    }
    proxy_codes = _load(PACKAGE_DIR / "proxy-codes.json")
    errors = _schema_problems(
        "proxy-codes.json", validators["proxy-codes.schema.json"], proxy_codes
    )
    problems.extend(
        errors
        or _check_other_codes(
            "proxy-codes.json", [row["code"] for row in proxy_codes["codes"]], taken
        )
    )
    proxy_rows = [] if errors else proxy_codes["codes"]
    warning_codes = _load(PACKAGE_DIR / "warning-codes.json")
    errors = _schema_problems(
        "warning-codes.json", validators["warning-codes.schema.json"], warning_codes
    )
    problems.extend(
        errors
        or _check_warning_codes(
            warning_codes,
            {**taken, "proxy-codes.json": {row["code"] for row in proxy_rows}},
            key_format_reasons(rules_schema),
        )
    )

    problems.extend(
        _check_scenarios(
            validators["scenario.schema.json"],
            openapi,
            registry=registry,
            strict=strict,
            actions={row["code"]: row for row in actions["codes"]},
            client_codes={row["code"]: row for row in client_rows},
            proxy_codes={row["code"]: row for row in proxy_rows},
            served_commit=source.get("commit") if isinstance(source, dict) else None,
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
