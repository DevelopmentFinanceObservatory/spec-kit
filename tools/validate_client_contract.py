# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jsonschema>=4.23",
#     "pyarrow>=15",
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
  `required` list and admits no null. An enum entry's property admits every
  value listed.
- `actions.json` lists codes in order, each once. Every active refusal code in
  `registries/codes.json` with a `problem_details` or `no_data_reason` mapping
  has a row, and no row names another code. A row carries `gap_kind` exactly
  when its code has a `no_data_reason` mapping. The `choose` action goes with
  the `choice` gap kind and only with it. A code with a `problem_details`
  mapping takes an action a problem document can carry.
- `client-codes.json` lists codes in order, each once, none of them a code in
  `actions.json` or `registries/codes.json`. Each row's class is one a
  scenario's error may name, other than a warning's, and only
  `refusal_without_code` takes the class its status maps to, and its
  retryable flag `by_status`. Each reason the key-lookup rule raises is a
  row of class `AuthenticationError`.
- `proxy-codes.json` and `warning-codes.json` list codes in order, each once,
  none of them a code another table or `registries/codes.json` holds, and the
  warning codes are those the client rules issue: `ref_digest_changed`,
  `route_deprecated`, `server_warning`, one `key_<reason>` per key-format
  reason, and each warning the series-left-out rule's vectors name.
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
  included, is an active code registered with its status or a code
  `proxy-codes.json` lists with its status. A document with a proxy code is
  `application/problem+json` and carries PROXY_PROBLEM_MEMBERS, its own
  status and `retryable` true, and no 202's operation reads `done`.
- Each error a scenario expects, other than a `SearchWarning` or
  `DataWarning`, carries a refusal's code from its exchanges, a failed
  operation's included, or a client code of its class.
- Each scenario's `expect` equals the outcomes its steps reach over its
  exchanges, with refusals' error classes and gap kinds from `actions.json`
  and the class of each error the client raises itself from
  `client-codes.json`, as `expected_outcomes` derives them, the waits
  before each poll included, under the record's base URL and again under
  REPLAY_ORIGIN with the recorded origin a response names moved there. A
  dataset's columns are the downloaded files' columns, read with pyarrow
  by `served_table` and ordered by `presented_columns`, which puts the
  columns whose values vary, as `same_value` judges cells, before those
  that hold one value, its rows and
  per-part row counts are left to the Parquet bodies, and each
  download hashes to its artifact's digest or the outcome says it does not. Each Search request a
  record holds posts the input the steps derive: the question, a choice's
  data_query or dataset_resubmit entries, or a member's identity with the
  entity groups of the roles its build left bound as they were, and each
  build posts the body `build_body` gives for the spec the step builds, its
  narrowing, its layout and, for a fetch or a mint, its party totals, and each Search request the whole body
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
- `scenarios/`, records and body files together, holds at most
  SCENARIOS_BUDGET bytes.
- Each ref example's file name matches its `name` and gets the verdict it
  states: from `dataset-ref.schema.json`, then from the rules that each
  dataset member's definition carries its own `spec_id` and that members hold
  distinct `spec_id`s. A ref example whose ref holds members names the
  recorded `scenario` that serves them, and each member's `spec_id`, `parts`,
  `content_version` and `sentence` are a dataset member's of that scenario,
  and its artifact digest is the digest of a file the scenario downloads.
  Each root of a derived member is a dataset member's `spec_id` and
  `content_version` of that scenario. Each member of an example the schema
  accepts records the rendering the scenario's build of its spec posted, and
  each root of a derived one that rendering in the frame layout.
- The derived members of the ref examples the schema accepts and of the
  output-comparison vectors agree wherever DERIVED_IDENTITY_RULES says one
  identity fixes another, so one `ocv_` names one op-spec and one output.
- Each rule file under `rules/` validates against `rules.schema.json`, names
  the rule its file name names, and names each vector once, each
  reading-order vector's `columns` and `rows` are what `presented_rows`
  gives, each file-selector vector's `query` is what `selector_query`
  gives, each output-digest vector's document holds its frame's rows, each
  operation's variable read as the name its `variables` give it, and
  its digest hashes that document, each minted name hashes its name
  document and each name it or a name document holds has one, and each
  growth vector's window starts before it ends. A missing or unreadable
  `rules/reading-order.json` or `rules/file-selector.json` is a problem.

It does not import `dfo_client_contract`, whose tests replay
the rule vectors. `tools/capture_client_contract.py` imports the outcome
rules from here, so the records are written by the code that checks them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache, partial
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import jsonschema
import pyarrow as pa
import pyarrow.parquet as pq
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "python" / "client-contract" / "src" / "dfo_client_contract"
REGISTRY_PATH = REPO_ROOT / "registries" / "codes.json"
# The bytes `scenarios/` may hold, records and body files together. Each test
# of this module copies the whole package, so the budget bounds their disk use.
SCENARIOS_BUDGET = 16_000_000

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
    """The value at an RFC 6901 `pointer` in `document`. Raises `KeyError`."""
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
    rules_schema: Mapping[str, Any],
) -> list[str]:
    """As `_check_other_codes`, and the codes are those a client's rules
    issue: `ref_digest_changed`, each header warning, one `key_<reason>`
    warning per key-format reason, and each warning the series-left-out
    rule issues."""
    codes = [row["code"] for row in document["codes"]]
    problems = _check_other_codes("warning-codes.json", codes, taken)
    wanted = {
        REF_DIGEST_CHANGED,
        ROUTE_DEPRECATED,
        SERVER_WARNING,
        *(f"key_{r}" for r in key_format_reasons(rules_schema)),
        *series_left_out_warnings(rules_schema),
    }
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


def series_left_out_warnings(rules_schema: Mapping[str, Any]) -> set[str]:
    """The warnings the series-left-out rule issues, or raises under strict."""
    vectors = rules_schema["$defs"]["series_left_out"]["properties"]["vectors"]
    members = vectors["items"]["properties"]
    return {*members["warnings"]["items"]["enum"], *members["raises"]["enum"]}


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
        pointer under `/paths`, and the operation it serves. None for a
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
    """A problem document's code: an active registry code registered with its
    status, or a code of the website proxy's own refusals that
    `proxy-codes.json` lists with its status, in a proxy problem document."""
    body = response.get("json")
    if response["status"] < 400 or not isinstance(body, dict) or "code" not in body:
        return []
    code = body["code"]
    proxy_row = proxy_codes.get(code) if isinstance(code, str) else None
    if proxy_row is not None:
        if proxy_row["status"] != response["status"]:
            return [
                (
                    f"{label}: proxy code {code!r} is not a code "
                    f"proxy-codes.json lists with status {response['status']}"
                )
            ]
        return _check_proxy_problem(label, response)
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


def _check_accepted(label: str, response: Mapping[str, Any]) -> list[str]:
    """A 202's operation does not read `done`, because a submission that finds a
    done operation builds it again."""
    body = response.get("json")
    operation = body.get("operation") if isinstance(body, dict) else None
    if response["status"] != 202 or not isinstance(operation, dict):
        return []
    if operation.get("status") == "done":
        return [f"{label}: a 202's operation reads done"]
    return []


# The members every problem document the website proxy answers itself carries.
PROXY_PROBLEM_MEMBERS = ("type", "title", "status", "detail", "code", "trace_id")
PROXY_PROBLEM_MEDIA = "application/problem+json"


def _check_proxy_problem(label: str, response: Mapping[str, Any]) -> list[str]:
    """A proxy problem document is `application/problem+json`, has the common
    members, carries the response's status, and has `retryable` true."""
    body = response["json"]
    problems = []
    content_type = response.get("headers", {}).get("content-type", "")
    if content_type.split(";", maxsplit=1)[0].strip().lower() != PROXY_PROBLEM_MEDIA:
        problems.append(
            f"{label}: a proxy problem document is not {PROXY_PROBLEM_MEDIA}"
        )
    missing = [m for m in PROXY_PROBLEM_MEMBERS if m not in body]
    if missing:
        problems.append(f"{label}: a proxy problem document lacks {missing}")
    if "status" in body and body["status"] != response["status"]:
        problems.append(f"{label}: a proxy problem document's status is not its own")
    if body.get("retryable") is not True:
        problems.append(f"{label}: a proxy problem document is not retryable: true")
    return problems


# --- outcomes ---------------------------------------------------------------
#
# A client's outcome for each step follows from the exchanges and from
# `actions.json`. The functions here derive it. The validator checks each
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
    "stop": "PermissionDeniedError",
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
# The retries a client makes by default after a retryable refusal.
MAX_RETRIES = 2
# The longest `Retry-After`, in seconds, a client waits before it tries again.
# A refusal that asks for longer raises at once.
RETRY_AFTER_MAX_S = 60.0
# The action of the refusals a client never retries, whatever their document
# and status say: a spent allowance clears only when it resets.
NOT_RETRIED_ACTION = "wait_for_reset"
# The status that cuts a synchronous build off at the server's limit.
BUILD_CUT_OFF = 504


def may_retry(
    request: Mapping[str, Any],
    responses: Sequence[Mapping[str, Any]],
    actions: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Whether a client sends `request` again after `responses`, the answers
    to it so far in order: the last is a refusal `tries_again` allows, the
    retries made stay under MAX_RETRIES, and for a build, no earlier answer
    was a 504 cut-off, after which the build is posted once more only. So a
    build refused 504 then 503 raises the 503, and one refused 503 then 504
    is posted once more after the 504."""
    *earlier, response = responses
    status = response["status"]
    headers = response.get("headers", {})
    if status < 400 or not tries_again(
        status, problem_body(response), headers, actions
    ):
        return False
    if len(earlier) >= MAX_RETRIES:
        return False
    build = request["method"] == "POST" and request.get("path") == "/datasets"
    return not (build and any(r["status"] == BUILD_CUT_OFF for r in earlier))


# The content types a client reads a refusal's body as a problem document in.
PROBLEM_MEDIA = ("application/problem+json", "application/json")


def problem_body(response: Mapping[str, Any]) -> Any:
    """A refusal's JSON body when its content type is one of PROBLEM_MEDIA,
    else None. A body in any other type reads as no body."""
    content_type = response.get("headers", {}).get("content-type", "")
    media = content_type.split(";", maxsplit=1)[0].strip().lower()
    return response.get("json") if media in PROBLEM_MEDIA else None


def gap_kinds(actions: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """The gap kind of each no_data reason in actions.json, keyed by code."""
    return {code: row["gap_kind"] for code, row in actions.items() if "gap_kind" in row}


def refusal_code(body: Any) -> str | None:
    """A refusal's code: its problem document's `code`."""
    if not isinstance(body, dict):
        return None
    code = body.get("code")
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


def retryable(status: int, body: Any, actions: Mapping[str, Mapping[str, Any]]) -> bool:
    """Whether a client retries a refusal: never when its code's action is
    NOT_RETRIED_ACTION, else as its problem document's `retryable` says, and
    otherwise by its status."""
    row = actions.get(refusal_code(body) or "")
    if row is not None and row["action"] == NOT_RETRIED_ACTION:
        return False
    if isinstance(body, dict) and isinstance(body.get("retryable"), bool):
        return body["retryable"]
    return status in RETRYABLE_STATUSES


def tries_again(
    status: int,
    body: Any,
    headers: Mapping[str, str],
    actions: Mapping[str, Mapping[str, Any]],
) -> bool:
    """Whether a client sends a refused request again, a poll included: the
    refusal is `retryable` and its `Retry-After`, when it carries one, is at
    most RETRY_AFTER_MAX_S."""
    wait = retry_after(headers)
    over_cap = wait is not None and wait > RETRY_AFTER_MAX_S
    return retryable(status, body, actions) and not over_cap


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


# The warning a client issues when a ref's file digest has changed.
REF_DIGEST_CHANGED = "ref_digest_changed"

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
# server's code.
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


def shared_cacheable(headers: Mapping[str, str]) -> bool:
    """Whether a response is marked `Cache-Control: public`. A shared cache
    replays it, so it carries no per-request trace id."""
    directives = headers.get("cache-control", "").lower().replace(" ", "").split(",")
    return "public" in directives


def untraced(headers: Mapping[str, str]) -> bool:
    """Whether an API response lacks the TRACE_HEADER header the server sends
    on every response but a shared-cacheable one."""
    return TRACE_HEADER not in headers and not shared_cacheable(headers)


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
# beside the warnings it raises under strict. Every other client code
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
    """`error`, raised on the API response whose trace id is `trace`. An
    error that describes a response, a RESPONSE_ERRORS code, SearchWarning
    or DataWarning, takes `trace` unless its trace id is settled. An error that
    describes the caller's input, or a storage-host fetch, keeps null."""
    if isinstance(error.trace_id, _Unset):
        reads = error.code in RESPONSE_ERRORS or error.error in WARNING_CLASSES
        error.trace_id = trace if reads else None
    return error


# --- reading members no strict pointer names ---------------------------------
#
# A member a client reads that no strict pointer names, and that holds a JSON
# type other than the one its rule reads, reads as absent. So does every
# member of a holder that is not an object. Each read of such a member goes
# through one of these.


def read_object(holder: Any, name: str) -> dict[str, Any] | None:
    """`holder`'s member `name` when it is an object, else None."""
    value = holder.get(name) if isinstance(holder, dict) else None
    return value if isinstance(value, dict) else None


def read_list(holder: Any, name: str) -> list[Any] | None:
    """`holder`'s member `name` when it is a list, else None."""
    value = holder.get(name) if isinstance(holder, dict) else None
    return value if isinstance(value, list) else None


def read_objects(holder: Any, name: str) -> list[dict[str, Any]]:
    """The object items of `holder`'s list member `name`: an absent list, or
    one of another type, holds none, and an item of another type reads as
    absent."""
    return [item for item in read_list(holder, name) or [] if isinstance(item, dict)]


def read_string(holder: Any, name: str) -> str | None:
    """`holder`'s member `name` when it is a string, else None."""
    value = holder.get(name) if isinstance(holder, dict) else None
    return value if isinstance(value, str) else None


# An `assumptions`, `reading_assumptions` or `operations` item reads only when
# it is an object whose required members are strings, and reads as the schema's
# members alone: an optional member of another type, and an unknown member,
# read as absent. A client posts these read forms back, in order.
ASSUMPTION_REQUIRED = ("kind", "binding_ref", "text")
OPERATION_REQUIRED = ("verb", "phrase")


def _required_strings(item: Any, names: tuple[str, ...]) -> dict[str, Any] | None:
    """`item`'s members `names` when it is an object holding each as a string,
    else None."""
    if not isinstance(item, dict) or not all(
        isinstance(item.get(name), str) for name in names
    ):
        return None
    return {name: item[name] for name in names}


def read_assumption(item: Any) -> dict[str, Any] | None:
    """An `Assumption` as a client reads it: kind, binding_ref and text, and
    refs as its string items when it is a list. None when the item does not
    read."""
    read = _required_strings(item, ASSUMPTION_REQUIRED)
    if read is not None and (refs := read_list(item, "refs")) is not None:
        read["refs"] = [ref for ref in refs if isinstance(ref, str)]
    return read


def read_operation(item: Any) -> dict[str, Any] | None:
    """An `Operation` as a client reads it: verb and phrase, over when it is a
    string or null, and target_year when it is null or a number other than a
    boolean with no fractional part, read as that integer, so 2020.0 reads as
    2020. None when the item does not read."""
    read = _required_strings(item, OPERATION_REQUIRED)
    if read is None:
        return None
    over = item.get("over")
    if "over" in item and (over is None or isinstance(over, str)):
        read["over"] = over
    year = item.get("target_year")
    if year is None and "target_year" in item:
        read["target_year"] = None
    elif (isinstance(year, int) and not isinstance(year, bool)) or (
        isinstance(year, float) and year.is_integer()
    ):
        read["target_year"] = int(year)
    return read


def read_items(
    holder: Any, name: str, read: Callable[[Any], dict[str, Any] | None]
) -> list[dict[str, Any]]:
    """The items of `holder`'s list member `name` that `read` reads, in
    order, each in its read form."""
    return [
        item for item in map(read, read_list(holder, name) or []) if item is not None
    ]


# The answer kinds a client reads. Any other kind raises ContractError.
ANSWER_KINDS = ("resolution", "catalogue", "declined")
RESOLUTION = "resolution"
# The variable text an identity resubmit sends for a member no answer part names.
UNNAMED_VARIABLE_TEXT = "dataset"
# The warning severities a fetch reports in its SearchWarning. `info` stays quiet.
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
    return Answer(
        body, parts, read_string(body, "rendered_sentence"), read_string(body, "status")
    )


def _readable(part: Any) -> bool:
    """Whether a part has a string `status`, and each spec it holds names
    its own spec_id in its definition."""
    if not isinstance(part, dict) or not isinstance(part.get("status"), str):
        return False
    specs = [part.get("spec")] if part["status"] == "definite" else []
    if part["status"] == "candidates":
        specs = read_list(read_object(part, "candidates"), "specs") or []
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
    candidates part's options are its candidates, chosen by index or by
    `candidate_label`. Any other gap's are its no_data options, chosen by
    index or label, a label scoped to the options that mention the word the
    part was named by. A label must match exactly one option. A boolean part
    or option is neither an index nor a label."""
    if isinstance(part, bool) or isinstance(option, bool):
        raise _refuse()
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
            i
            for i, p in enumerate(answer.parts)
            if read_string(p, "variable_text") == part
        ]
        if not named:
            mention = part
            named = [
                i
                for i in sorted(gap_parts)
                if any(part in mentions(o) for o in _options(answer, i))
            ]
        if len(named) != 1:
            raise _refuse()
        index = named[0]
    if index not in gap_parts:
        raise _refuse()

    chosen = answer.parts[index]
    if chosen.get("status") == "candidates":
        specs = chosen["candidates"]["specs"]
        if isinstance(option, int):
            picked = [specs[option]] if 0 <= option < len(specs) else []
        else:
            picked = [spec for spec in specs if candidate_label(spec) == option]
        if len(picked) != 1:
            raise _refuse()
        return Choice(index, candidate=picked[0])
    options = _options(answer, index)
    if isinstance(option, int):
        found = [options[option]] if 0 <= option < len(options) else []
    else:
        found = [
            o
            for o in options
            if read_string(o, "label") == option
            and (mention is None or mention in mentions(o))
        ]
    if len(found) != 1:
        raise _refuse()
    return Choice(index, option=found[0])


def candidate_label(spec: Mapping[str, Any]) -> str | None:
    """A candidate's label: the `text` of its spec's `resolved.sentence_plan`
    tokens in the `heading` segment, a token whose `segment` is not a string
    counted in it, joined and stripped of surrounding white space; else its
    `resolved.rendered_sentence`; else None."""
    resolved = read_object(spec, "resolved")
    heading = "".join(
        read_string(token, "text") or ""
        for token in read_objects(resolved, "sentence_plan")
        if read_string(token, "segment") in (None, "heading")
    ).strip()
    return heading or read_string(resolved, "rendered_sentence") or None


def _options(answer: Answer, index: int) -> list[dict[str, Any]]:
    """A part's no_data options, as `read_objects` reads them."""
    return read_objects(read_object(answer.parts[index], "no_data"), "options")


def mentions(option: Mapping[str, Any]) -> list[str]:
    """The words a no_data option mentions, its string items."""
    return [w for w in read_list(option, "mentions") or [] if isinstance(w, str)]


def choice_input(answer: Answer, choice: Choice) -> dict[str, Any]:
    """The Search input a choice posts. In a one-part answer an option posts
    its data_query. Any other choice posts dataset_resubmit with one entry
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
                ordering=read_string(candidates, "ordering"),
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
    """The one part a no_data option's `data_query` holds. A query of other
    than one part raises ContractError, in a one-part answer as in a
    several-part one."""
    parts = _data_query(option).get("parts")
    if not isinstance(parts, list) or len(parts) != 1:
        raise ClientError("contract_violation")
    return parts[0]


def reading_echo(part: Mapping[str, Any], spec: Mapping[str, Any]) -> Any:
    """The assumptions a resubmitted reading of `part` echoes: a definite
    part's `assumptions`, or a candidates part's `reading_assumptions` under
    the reading's spec_id, each in its read form and an item that does not
    read left out.
    None, when the part carries no such list, posts no `assumptions`, and
    Search reads the reading as a fresh request does. `[]` states none."""
    if part.get("status") == "candidates":
        holder, name = read_object(part, "reading_assumptions"), spec["spec_id"]
    else:
        holder, name = part, "assumptions"
    if read_list(holder, name) is None:
        return None
    return read_items(holder, name, read_assumption)


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
    groups = read_object(spec.get("resolved"), "entity_groups")
    if groups:
        item["entity_groups"] = groups
    if assumptions is not None:
        item["assumptions"] = assumptions
    return item


def _echo(part: dict[str, Any], **members: Any) -> dict[str, Any]:
    """An entry with the members every entry but a parsed one echoes from its
    part: variable_text, separate when true, and operations when any item
    reads, in its read form."""
    variable_text = read_string(part, "variable_text")
    if not variable_text:
        raise _refuse()
    entry: dict[str, Any] = {**members, "variable_text": variable_text}
    if part.get("separate") is True:
        entry["separate"] = True
    if operations := read_items(part, "operations", read_operation):
        entry["operations"] = operations
    return entry


def reading_hints(answer: Answer) -> dict[str, str | None]:
    """How `answer`'s readings differ from the question, each line a client
    shows once, with its code. In part order, each definite part gives each
    operation Search did not run, coded by its verb, each departure, and each
    warning shown at `warn` or `degraded` severity; the answer's diagnostics
    warnings follow. An `info` warning gives nothing."""
    hints: dict[str, str] = {}

    def notices(holder: Any) -> None:
        for warning in read_objects(holder, "warnings"):
            message = read_string(warning, "message")
            if read_string(warning, "severity") in SHOWN_SEVERITIES and message:
                hints.setdefault(f"Note: {message}", read_string(warning, "code"))

    for part in answer.parts:
        if part.get("status") != "definite":
            continue
        for operation in read_items(part, "operations", read_operation):
            if verb := operation["verb"]:
                phrase = json.dumps(operation["phrase"], ensure_ascii=False)
                hints.setdefault(f"Not done: {verb} ({phrase})", verb)
        for departure in read_objects(part, "departures"):
            if text := read_string(departure, "text"):
                hints.setdefault(text, read_string(departure, "code"))
        notices(part)
    notices(read_object(answer.body, "diagnostics"))
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
    """Each part without a reading. A candidates part is a choice among its
    specs, a no_data part takes its reason's gap kind, an unsupported part is
    an `unsupported` gap, and a part of unknown status is an `unknown` gap."""
    gaps = []
    for index, part in enumerate(answer.parts):
        match part.get("status"):
            case "definite":
                continue
            case "candidates":
                kind, options = "choice", len(part["candidates"]["specs"])
            case "no_data":
                no_data = part["no_data"]
                kind = kinds.get(no_data["reason"], "no_data")
                options = len(read_objects(no_data, "options"))
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
        if variable_text := read_string(part, "variable_text"):
            named.append(variable_text)
    return list(members.values())


def held_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """A served spec as a client holds it, the shape of a step's `spec`: its
    spec_id, definition and sentence, and the entity groups its `resolved`
    names, when it names any."""
    resolved = spec["resolved"]
    held = {
        "spec_id": spec["spec_id"],
        "definition": spec["definition"],
        "sentence": read_string(resolved, "rendered_sentence"),
    }
    if groups := read_object(resolved, "entity_groups"):
        held["entity_groups"] = groups
    return held


def answer_outcome(answer: Answer, kinds: Mapping[str, str]) -> dict[str, Any]:
    """An answer outcome. A declined or catalogue answer is incomplete."""
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
    members. It is the member's parts joined by `; `, or its sentence when it
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
    """The JSON type of a parsed value, a number with no fractional part as
    `integer`, as JSON Schema reads it, so `1.0` is an integer, as it is to a
    client whose JSON parser keeps no separate integer type."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return "integer"
    for kind, name in ((dict, "object"), (list, "array"), (str, "string")):
        if isinstance(value, kind):
            return name
    return "number" if isinstance(value, float) else "null"


def schema_types(openapi: dict[str, Any], schema: Any) -> set[str] | None:
    """The JSON types `schema` admits, through `$ref`, `anyOf`, `oneOf` and
    `allOf`, with `number` admitting an integer. None when it names none."""
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
    type their schema does not admit, and each item of a strict list of a
    type its item schema does not admit, read through `schema`. Each component
    schema it reaches names its own, and a
    discriminated union whose discriminator `body` lacks is a gap. The walk
    goes down only through strict members, the ones a client reads, so a
    member no client reads, such as a spec's `resolved.equivalent_readings`,
    goes unchecked. A discriminator value the union does not map, and a
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
        types = schema_types(openapi, schema["items"])
        for index, item in enumerate(body):
            if types is not None and json_type(item) not in types:
                gaps.append(f"{where}/{index}")
                continue
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
# The rendering member a fetch posts as true unless the caller keeps party
# totals, which leaves out the group totals of the identity's open party roles.
WITHOUT_PARTY_TOTALS = "without_party_totals"
# The column a client presents last, the column a stacked dataset puts
# first, the measure no row sorts by, and the sort key that comes last.
STAT_VAR = "stat_var"
PART = "part"
VALUE = "value"
TIME_PERIOD = "time_period"
# The snapshot refusal after which a stored version's lookup builds it again.
# The version exists, but its file has expired. Any other refusal ends it.
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
    """The rendering for `layout`, long by default, with party totals
    kept."""
    return dict(LAYOUT_RENDERINGS[layout or "long"])


def fetch_rendering(
    layout: str | None, *, party_totals: bool = False
) -> dict[str, Any]:
    """The build request's `options` for a fetch of an answer or a spec, or
    a mint: the layout's rendering, with `without_party_totals` unless the
    caller keeps party totals."""
    rendering = rendering_for(layout)
    if not party_totals:
        rendering[WITHOUT_PARTY_TOTALS] = True
    return rendering


def resumed_rendering(
    layout: str | None, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """The rendering a resumed operation's build posted, as its done
    manifest records it: the layout's rendering, with `without_party_totals`
    when the artifact for that rendering carries the cut."""
    rendering = rendering_for(layout)
    artifact = pick_artifact(manifest, rendering)
    found = artifact.get("rendering") if artifact is not None else None
    if isinstance(found, dict) and found.get(WITHOUT_PARTY_TOTALS) is True:
        rendering[WITHOUT_PARTY_TOTALS] = True
    return rendering


@dataclass(frozen=True, slots=True)
class ServedTable:
    """A served table as the reading-order rule reads it: its columns in the
    order the server wrote them, its row count, and the value of each column
    that holds one value in every row. A column `same` leaves out varies.
    `nested` names the columns holding lists or structs, which no row is
    sorted by."""

    columns: tuple[str, ...]
    rows: int
    same: Mapping[str, Any]
    nested: frozenset[str] = frozenset()


def same_value(left: Any, right: Any) -> bool:
    """Whether two cells hold the same value, as the reading-order rule
    judges it: numbers by value, so 1 and 1.0 are the same, booleans apart
    from numbers, NaN the same as NaN, null the same as null, lists item by
    item in order, and structs field by field in any order."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (math.isnan(left) and math.isnan(right))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(map(same_value, left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            same_value(value, right[key]) for key, value in left.items()
        )
    return left == right


def _one_value(values: Sequence[Any]) -> bool:
    """Whether `values` hold one value as `same_value` judges it."""
    return all(same_value(value, values[0]) for value in values[1:])


def table_from_rows(
    columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> ServedTable:
    """The `ServedTable` of a table given as rows, as a vector gives one."""
    same = {
        name: rows[0][index]
        for index, name in enumerate(columns)
        if rows and _one_value([row[index] for row in rows])
    }
    nested = frozenset(
        name
        for index, name in enumerate(columns)
        if any(isinstance(row[index], list | dict) for row in rows)
    )
    return ServedTable(tuple(columns), len(rows), same, nested)


@lru_cache(maxsize=64)
def served_table(data: bytes) -> ServedTable:
    """The `ServedTable` of a downloaded Parquet file. Each scenario is
    replayed twice and each test validates the whole contract, so the
    tables are read once per file content.

    Raises:
        _UnfitError: The file is not a Parquet file.
    """
    try:
        table = pq.read_table(pa.BufferReader(data))
    except (pa.ArrowException, OSError) as exc:
        raise _UnfitError(
            f"a downloaded file is unreadable: not Parquet: {exc}"
        ) from exc
    nested = frozenset(
        field.name for field in table.schema if pa.types.is_nested(field.type)
    )
    # Every column is judged by `same_value`, as `table_from_rows` judges a
    # vector's, so a vector and its Parquet file group their columns alike.
    same = {}
    for name in table.column_names if table.num_rows else ():
        values = table.column(name).to_pylist()
        if _one_value(values):
            same[name] = values[0]
    return ServedTable(tuple(table.column_names), table.num_rows, same, nested)


def filled_slots(
    columns: Collection[str], reading: Sequence[Sequence[str]]
) -> list[str]:
    """The reading slots `columns` fill, in slot order: each slot's first name
    the columns hold."""
    return [
        name
        for names in reading
        if (name := next((n for n in names if n in columns), None)) is not None
    ]


def _varies(name: str, tables: Sequence[ServedTable]) -> bool:
    """Whether column `name` holds more than one value over the rows of
    `tables` stacked, a table without the column holding null in it."""
    values = []
    for table in tables:
        if not table.rows:
            continue
        if name in table.columns and name not in table.same:
            return True
        values.append(table.same.get(name))
    return not _one_value(values)


@dataclass(frozen=True, slots=True)
class ColumnGroups:
    """A dataset's columns in the reading-order rule's groups: `part` when
    there are several members, the filled reading slots in slot order, the
    other columns whose values differ between the stacked rows and those
    that hold one value in every row, each in served order, and `stat_var`
    with whether it varies."""

    part: tuple[str, ...]
    first: tuple[str, ...]
    varying: tuple[str, ...]
    same: tuple[str, ...]
    last: tuple[str, ...]
    last_varies: bool
    nested: frozenset[str] = frozenset()

    @property
    def columns(self) -> list[str]:
        return [*self.part, *self.first, *self.varying, *self.same, *self.last]

    @property
    def sort_keys(self) -> list[str]:
        """The columns a client sorts each member's rows by: the filled
        reading slots in slot order without `value`, a measure, and without
        the time period, then the varying columns in presented order,
        `stat_var` among them when it varies, then the time period, so each
        series' years read together. A column of lists or structs is no
        key."""
        keys = [c for c in self.first if c not in (VALUE, TIME_PERIOD)]
        keys += [*self.varying, *(self.last if self.last_varies else ())]
        keys += [TIME_PERIOD] * (TIME_PERIOD in self.first)
        return [key for key in keys if key not in self.nested]


def column_groups(
    tables: Sequence[ServedTable], reading: Sequence[Sequence[str]]
) -> ColumnGroups:
    """The reading-order rule's column groups for a dataset whose members
    serve `tables`. The served order gathers the members' columns in member
    order, each once."""
    gathered = list(dict.fromkeys(c for table in tables for c in table.columns))
    first = filled_slots(gathered, reading)
    rest = [c for c in gathered if c not in first and c != STAT_VAR]
    varying = [c for c in rest if _varies(c, tables)]
    return ColumnGroups(
        part=(PART,) if len(tables) > 1 else (),
        first=tuple(first),
        varying=tuple(varying),
        same=tuple(c for c in rest if c not in varying),
        last=(STAT_VAR,) if STAT_VAR in gathered else (),
        last_varies=STAT_VAR in gathered and _varies(STAT_VAR, tables),
        nested=frozenset().union(*(table.nested for table in tables)),
    )


def presented_columns(
    tables: Sequence[ServedTable], reading: Sequence[Sequence[str]]
) -> list[str]:
    """The columns a client presents for a dataset whose members serve
    `tables`, as `column_groups` orders them."""
    return column_groups(tables, reading).columns


def presented_rows(
    members: Sequence[tuple[str | None, Sequence[str], Sequence[Sequence[Any]]]],
    reading: Sequence[Sequence[str]],
) -> tuple[list[str], list[list[Any]]]:
    """The columns and rows a client presents for members given as their
    `part` value, served columns and served rows. Each member's rows are
    sorted by `ColumnGroups.sort_keys`, ascending with nulls last and ties in served
    order, and the members follow in member order. A row of a stacked
    dataset holds null in a column its member lacks."""
    tables = [table_from_rows(columns, rows) for _, columns, rows in members]
    groups = column_groups(tables, reading)
    columns, keys = groups.columns, groups.sort_keys
    presented = []
    for part, served, rows in members:
        at = {name: index for index, name in enumerate(served)}
        cells = [
            {**{name: row[index] for name, index in at.items()}, PART: part}
            for row in rows
        ]
        ordered = sorted(
            cells,
            key=lambda cell: [(cell.get(key) is None, cell.get(key)) for key in keys],
        )
        presented.extend([cell.get(name) for name in columns] for cell in ordered)
    return columns, presented


def load_reading_columns(package_dir: Path) -> list[list[str]] | None:
    """The reading slots `rules/reading-order.json` lists, or None when the
    file does not hold them, which the validator reports."""
    try:
        reading = _load(package_dir / "rules" / "reading-order.json")["reading_columns"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(reading, list) and all(
        isinstance(names, list) and all(isinstance(n, str) for n in names)
        for names in reading
    ):
        return reading
    return None


def load_selector_keys(package_dir: Path) -> list[str] | None:
    """The query keys `rules/file-selector.json` lists, or None when the file
    does not hold them, which the validator reports."""
    try:
        keys = _load(package_dir / "rules" / "file-selector.json")["query_keys"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if isinstance(keys, list) and keys and all(isinstance(k, str) for k in keys):
        return keys
    return None


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


# The versions a client asks the versions route for on each page.
VERSIONS_PAGE = 100


def versions_path(spec_id: str, cursor: str | None = None) -> str:
    """One page of the versions route, VERSIONS_PAGE versions newest first,
    continuing after `cursor` when one is given."""
    path = f"/datasets/{spec_id}/versions?limit={VERSIONS_PAGE}"
    return path if cursor is None else f"{path}&cursor={quote(cursor, safe='')}"


def search_body(input_: Mapping[str, Any]) -> dict[str, Any]:
    """The whole body a client posts to Search for `input_`."""
    return {"schema_version": 1, "input": input_}


def _listed_version(version: Any) -> bool:
    return (
        isinstance(version, dict)
        and isinstance(version.get("content_version"), str)
        and "base_content_version" in version
        and isinstance(version["base_content_version"], str | None)
    )


def newest_plain(page: Callable[[str | None], Any]) -> str | None:
    """The newest stored version, the first the versions route lists with no
    `base_content_version`, a plain build and never a version derived from
    one, or None when the list ends without one. `page` answers the page
    after a cursor, the first for None, and the walk follows `next_cursor`
    until it is null. Each page is read whole before any version on it is
    taken: a page whose `versions` is not a list of versions, each with a
    `content_version` and a `base_content_version` that is a string or
    null, or whose `next_cursor` is neither a string nor null, raises
    ContractError, even when it lists a plain version."""
    cursor: str | None = None
    while True:
        body = page(cursor)
        versions = body.get("versions") if isinstance(body, dict) else None
        cursor = body.get("next_cursor") if isinstance(body, dict) else None
        if (
            not isinstance(versions, list)
            or not all(map(_listed_version, versions))
            or not isinstance(cursor, str | None)
        ):
            raise ClientError("contract_violation")
        for version in versions:
            if version["base_content_version"] is None:
                return version["content_version"]
        if cursor is None:
            return None


def stored_version[T](
    member: Mapping[str, Any],
    client_codes: Mapping[str, Any],
    *,
    snapshot: Callable[[], Any],
    stored_file: Callable[[Any], tuple[T | None, ClientError | None]],
    rebuild: Callable[[], Any],
    rebuilt: Callable[[Any], T],
    newest: Callable[[], str | None],
) -> T:
    """One version a ref names, through the lookup order: the snapshot
    route and the files route at the stored digest, then the file
    selector, then a build that must answer the same version. A snapshot
    or build that answers another spec_id raises ContractError, as a
    snapshot of another version does. A snapshot refusal other than
    artifact_expired ends the lookup. Otherwise a lookup that finds no file
    raises VersionUnavailableError with the first refusal's code, or
    version_unavailable, and the newest version, either the one the build
    answered or the versions route's first.

    The callers make the requests: `snapshot` asks the snapshot route,
    `stored_file` gives what the files route or the selector served for a
    snapshot manifest, or None and the refusal it met, `rebuild` builds the
    member's spec, `rebuilt` gives what a build of the stored version
    serves, and `newest` asks the versions route."""
    spec_id, version = member["spec"]["spec_id"], member["content_version"]
    refusal: ClientError | None = None
    manifest = None
    try:
        manifest = snapshot()
    except ClientError as error:
        if error.code != FILE_EXPIRED:
            raise
        refusal = error
    if manifest is not None:
        built = (manifest["definition"]["spec_id"], manifest["content_version"])
        if built != (spec_id, version):
            raise ClientError("contract_violation")
        found, refusal = stored_file(manifest)
        if found is not None:
            return found
    try:
        manifest = rebuild()
    except ClientError as error:
        if error.error_class(client_codes) not in REBUILD_REFUSED:
            raise
        refusal = refusal if refusal is not None else error
        newest_version = newest()
    else:
        if manifest["definition"]["spec_id"] != spec_id:
            raise ClientError("contract_violation")
        if manifest["content_version"] == version:
            return rebuilt(manifest)
        newest_version = manifest["content_version"]
    raise ClientError(
        refusal.code if refusal is not None else "version_unavailable",
        error=VERSION_UNAVAILABLE,
        newest_version=newest_version,
        trace_id=refusal.trace_id if refusal is not None else None,
    )


def build_body(
    spec: Mapping[str, Any],
    scope: Mapping[str, Any] | None,
    rendering: Mapping[str, Any],
) -> dict[str, Any]:
    """The body a client posts to build `spec`, a step's `spec` or a ref's.
    It holds the spec_id and unmodified definition, `scope` when it narrows
    anything, and `rendering` as the options."""
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


def selector_query(rendering: Mapping[str, Any], keys: Sequence[str]) -> str:
    """The file selector's query for `rendering`, as the file-selector rule
    builds it: each of `keys` the rendering holds, in that order."""
    return "&".join(
        f"{key}={_query_value(rendering[key])}" for key in keys if key in rendering
    )


def _query_value(value: Any) -> str:
    """A query value, a boolean written `true` or `false`."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def selector_path(
    spec_id: str,
    content_version: str,
    rendering: Mapping[str, Any],
    keys: Sequence[str],
) -> str:
    """The file selector's path for a stored version in `rendering`, its
    query from `selector_query`."""
    return (
        f"/datasets/{spec_id}/{content_version}/file?{selector_query(rendering, keys)}"
    )


# The storage host a signed download names, the only host outside the API a
# client fetches from, and the query every signed URL in a record carries.
STORAGE_ORIGIN = "https://storage.googleapis.com"
REDACTED_QUERY = "X-Goog-Signature=redacted"
# A files route answers FILES_ROUTE_REDIRECT with the `location` the client
# fetches, or FILES_ROUTE_FILE with the file as its body. A refusal reads by
# the refusal rules, and any other status, another 2xx or 3xx included,
# raises ContractError `unexpected_response`. A signed fetch that answers
# other than SIGNED_FETCH_STATUS is a failed fetch.
FILES_ROUTE_REDIRECT = 302
FILES_ROUTE_FILE = 200
SIGNED_FETCH_STATUS = 200


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
    above `info` severity, with that warning's code. None when it has none."""
    for warning in read_objects(manifest, "warnings"):
        if read_string(warning, "severity") != "info":
            return ClientError(read_string(warning, "code"), error="DataWarning")
    return None


def resubmitted_sentence(answer: Answer, spec_id: str) -> str:
    """The sentence Search gives a built identity it was posted. The answer
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
    """The held spec's entity groups a built definition keeps, which are the
    group of each role the built identity binds to the same entities the held
    definition binds it to. A role the narrowing rebound or dropped loses its
    group, and a window or facets narrowing keeps every group."""
    if held is None or not (groups := read_object(held, "entity_groups")):
        return {}
    before = read_object(read_object(held["definition"], "identity"), "entities")
    after = read_object(built["identity"], "entities")
    return {
        role: group
        for role, group in groups.items()
        if read_list(after, role) is not None
        and bound_refs(after, role) == bound_refs(before, role)
    }


def bound_refs(entities: dict[str, Any] | None, role: str) -> set[str]:
    """The refs an identity's `entities` binds `role` to, its string items.
    A binding of another type, or an item of one, reads as absent."""
    return {ref for ref in read_list(entities, role) or [] if isinstance(ref, str)}


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
        return error, retryable(500, None, actions)
    status = problem.get("status")
    status = status if isinstance(status, int) else 500
    error = refusal_error(status, problem, actions, headers)
    return error, retryable(status, problem, actions)


class LongBuild:
    """A build the server runs on, followed the way a client polls it. Each
    wait before a poll is `PollPacing`'s. A retryable 429 poll spends no
    retry, any other retryable refused poll one of MAX_RETRIES, and a refused
    poll that is not retryable raises, a 429 included. The poll after a
    refusal waits as `PollPacing` gives. A poll answering a failed
    operation, retryable by its error, posts the build again once when the
    client holds its request. Otherwise, and for an operation that failed
    before the client polled it, the operation's error raises. `entry` is
    the build's `long_builds` entry, and `result` its manifest once it is
    done."""

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
            again = retryable(status, body, self.actions)
            if error.code != OPERATION_FAILED:
                if not tries_again(status, body, headers, self.actions):
                    raise error
                if status != 429:
                    if self.retries >= MAX_RETRIES:
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
    and the rendering the build posted, or for a resumed operation None and
    the layout whose rendering `resumed_rendering` reads from the done
    manifest."""

    resource: dict[str, Any]
    headers: dict[str, str]
    request: dict[str, Any] | None
    spec: dict[str, Any] | None
    rendering: dict[str, Any] | None
    layout: str | None = None


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
    reading: Sequence[Sequence[str]] | None
    selector_keys: Sequence[str] | None
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
        exchange of the run is the one acted on. A run that ends while
        `may_retry` holds lacks a retry the client makes. Each API
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
            and may_retry(request, answers, self.actions)
        ):
            headers = exchange["response"].get("headers", {})
            self.retry_waits.append(retry_wait(headers, len(answers) - 1))
            exchange = self.exchanges[self.cursor]
            self.cursor += 1
            self.took(exchange)
            answers.append(exchange["response"])
        if retries and may_retry(request, answers, self.actions):
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
            path = body_path(self.body_dir, response["body_file"])
            if not path.is_file():
                raise _UnfitError(f"body file {response['body_file']!r} is missing")
            return path.read_bytes()
        if "json" in response:
            return json.dumps(response["json"]).encode()
        return b""

    def download(self, target: str, digest: str, *, origin: bool) -> bytes:
        """The bytes of a file through its files route, named by an `origin_path`, as
        `artifact_route` places an artifact's `url`, when `origin`, else by a
        path under the base URL. A 302 names the signed URL in its
        `location`, which the client fetches once where `download_target`
        places it. A 200 holds the file, a refusal raises, and any other
        status raises ContractError `unexpected_response`. A fetch that
        answers other than 200, or bytes that do not hash to `digest`, asks
        the route once more, and a second failure raises."""
        failure = ClientError("download_failed")
        base = urlsplit(self.base_url)
        route_url = (
            f"{base.scheme}://{base.netloc}{target}"
            if origin
            else f"{self.base_url.rstrip('/')}{target}"
        )
        for _ in range(2):
            kind = "origin_path" if origin else "path"
            files = self.call("GET", target, kind=kind)["response"]
            status = files["status"]
            if status >= 400:
                raise refusal_error(
                    status, problem_body(files), self.actions, files.get("headers")
                )
            if status == FILES_ROUTE_FILE:
                file = files
            elif status == FILES_ROUTE_REDIRECT:
                location = files.get("headers", {}).get("location")
                if not isinstance(location, str):
                    raise _UnfitError(f"the redirect from {target} names no location")
                fetched, at = download_target(self.base_url, route_url, location)
                file = self.call("GET", at, kind=fetched, retries=False)["response"]
                if file["status"] != SIGNED_FETCH_STATUS:
                    failure = ClientError("download_failed")
                    continue
            else:
                raise ClientError("unexpected_response")
            data = self.body(file)
            if digest_of(data) == digest:
                return data
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
        options and `scope` as its input's scope. It returns the manifest the
        build answers, through a long build when it answers 202."""
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
        data = self.download(route, artifact["artifact_digest"], origin=True)
        return {
            "parts": parts,
            "spec_id": built,
            "content_version": manifest["content_version"],
            "sentence": sentence,
            "served": served_table(data),
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
        """One version a ref names, through the lookup order
        `stored_version` states."""
        spec = member["spec"]
        spec_id, version = spec["spec_id"], member["content_version"]
        rendering = member["artifact"]["rendering"]
        stored = {
            "parts": member["parts"],
            "spec_id": spec_id,
            "content_version": version,
            "sentence": member["sentence"],
        }

        def stored_file(
            manifest: dict[str, Any],
        ) -> tuple[dict[str, Any] | None, ClientError | None]:
            data, refusal = self.stored_file(member, manifest)
            if data is None:
                return None, refusal
            return {**stored, "served": served_table(data)}, None

        def rebuilt(manifest: dict[str, Any]) -> dict[str, Any]:
            held = {**spec, "sentence": member["sentence"]}
            return self.member(member["parts"], held, manifest, rendering, False)

        return stored_version(
            member,
            self.client_codes,
            snapshot=lambda: self.acted_on(
                self.call("GET", f"/datasets/{spec_id}/{version}")
            ),
            stored_file=stored_file,
            rebuild=lambda: self.build(spec, rendering),
            rebuilt=rebuilt,
            newest=lambda: self.newest(spec_id),
        )

    def stored_file(
        self, member: dict[str, Any], manifest: dict[str, Any]
    ) -> tuple[bytes | None, ClientError | None]:
        """The stored file the files route at the stored digest, or else the
        file selector, served, or None, and the files route's refusal when
        it refused. A selector refusal outside FILE_GONE, and a files route
        one, raise. A selector file of another digest is not the stored one."""
        digest = member["artifact"]["digest"]
        refusal = None
        artifact = next(
            (a for a in manifest["artifacts"] if a["artifact_digest"] == digest), None
        )
        if artifact is not None:
            try:
                route = artifact_route(self.base_url, artifact["url"])
                return self.download(route, digest, origin=True), None
            except ClientError as error:
                if error.code not in FILE_GONE:
                    raise
                refusal = error
        if self.selector_keys is None:
            raise _UnfitError("rules/file-selector.json lists no query_keys")
        path = selector_path(
            member["spec"]["spec_id"],
            member["content_version"],
            member["artifact"]["rendering"],
            self.selector_keys,
        )
        try:
            return self.download(path, digest, origin=False), None
        except ClientError as error:
            if error.code == "digest_mismatch":
                return None, refusal
            if error.code in FILE_GONE:
                return None, refusal or error
            raise

    def newest(self, spec_id: str) -> str | None:
        """The newest stored version, as `newest_plain` reads the versions
        route, or None when the route refuses the spec as unknown."""
        try:
            return newest_plain(
                lambda cursor: self.acted_on(
                    self.call("GET", versions_path(spec_id, cursor))
                )
            )
        except ClientError as error:
            if error.error_class(self.client_codes) in NO_VERSIONS:
                return None
            raise

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
        """A fetch of the answer. Before any request, a declined or catalogue
        answer raises DeclinedError, a gap raises IncompleteAnswerError unless
        the fetch is partial and some part has a reading, a strict fetch whose
        readings depart from the question raises SearchWarning, and the frame
        layout with several members raises InvalidRequestError."""
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
        rendering = fetch_rendering(
            step.get("layout"), party_totals=step.get("party_totals", False)
        )
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
        return self.dataset(members, gaps, search_warning=warns)

    def _fetch_spec(self, step: dict[str, Any]) -> dict[str, Any]:
        rendering = fetch_rendering(
            step.get("layout"), party_totals=step.get("party_totals", False)
        )
        manifest = self.build(step["spec"], rendering, posted_scope(step))
        strict = step.get("strict", False)
        member = self.member([], step["spec"], manifest, rendering, strict)
        return self.dataset([member], [])

    def _fetch_ref(self, step: dict[str, Any]) -> dict[str, Any]:
        members = [self.stored_version(member) for member in step["ref"]["members"]]
        return self.dataset(members, [])

    def _mint(self, step: dict[str, Any]) -> dict[str, Any]:
        rendering = fetch_rendering(
            step.get("layout"), party_totals=step.get("party_totals", False)
        )
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
        self.minted = _Minted(resource, headers, None, None, None, step.get("layout"))
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
        rendering = minted.rendering or resumed_rendering(minted.layout, manifest)
        member = self.member([], minted.spec, manifest, rendering, False)
        return self.dataset([member], [])

    def dataset(
        self,
        members: Sequence[Mapping[str, Any]],
        gaps: Sequence[Mapping[str, Any]],
        *,
        search_warning: bool | None = None,
    ) -> dict[str, Any]:
        """A dataset outcome as `dataset_summary` gives it, with the columns a
        client presents: the members' served columns ordered by the
        reading-order rule, as `presented_columns` gives them. Without the
        rule's reading slots, which the validator reports, the outcome gives
        no columns."""
        summary = dataset_summary(members, gaps, search_warning=search_warning)
        if self.reading is None:
            return {"dataset": summary}
        columns = presented_columns([m["served"] for m in members], self.reading)
        return {"dataset": {"columns": columns, **summary}}


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
    reading: Sequence[Sequence[str]] | None,
    selector_keys: Sequence[str] | None,
) -> list[dict[str, Any]]:
    """The outcome each of `record`'s steps reaches over its exchanges, its
    binary bodies read from `body_dir`, over
    `actions` for refusals' error classes and gap kinds, and over
    `client_codes` for the class of each error the client raises itself. A
    dataset outcome holds what `dataset_summary` gives and the columns the
    `reading` slots order from the downloaded files, without rows.

    Raises:
        ValueError: The exchanges do not fit the steps, or deriving an
            outcome raised, so the record names its own problem and the
            other records are checked.
    """
    replay = _Replay(
        record["exchanges"],
        routes,
        base_url,
        _strict_members(strict),
        actions,
        client_codes,
        body_dir,
        reading,
        selector_keys,
    )
    try:
        outcomes = [replay.step(step) for step in record["call"]]
    except (_UnfitError, KeyError, IndexError, StopIteration, ValueError) as exc:
        raise ValueError(f"the exchanges do not fit the steps: {exc!r}") from exc
    # Any other exception is a malformed record the rules missed. It is this
    # record's problem, and the other records are checked as before.
    except Exception as exc:
        raise ValueError(f"deriving its outcomes raised {exc!r}") from exc
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


# A body file's name, `scenario.schema.json`'s `body_file` pattern: a bare
# file name, so it is never absolute and never leaves its directory.
BODY_FILE_NAME = re.compile(
    _load(PACKAGE_DIR / "scenario.schema.json")["$defs"]["response"]["properties"][
        "body_file"
    ]["pattern"]
)


def body_path(directory: Path, body_file: str) -> Path:
    """The path of the body file `body_file` beside the records in
    `directory`, checked before anything reads or writes it.

    Raises:
        ValueError: `body_file` is not a bare file name, so it could be
            absolute or resolve outside `directory`, or it names a symlink,
            which would read or write the file it points to.
    """
    if BODY_FILE_NAME.fullmatch(body_file) is None:
        raise ValueError(
            f"body file {body_file!r} is not a bare file name in {directory}"
        )
    path = directory / body_file
    if path.is_symlink():
        raise ValueError(f"body file {body_file!r} in {directory} is a symlink")
    return path


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
    with the redacted query. A `location` may also name a route under
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
        try:
            path = body_path(directory, body_file)
        except ValueError as exc:
            problems.append(f"scenarios/{file_name}: exchange {index}: {exc}")
            continue
        if not path.is_file():
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
    outcome's columns are derived from the downloaded files, and its rows
    and per-part row counts are not, except that several members put the
    `part` column first and list each member's `part` value, rows summing
    to the dataset's."""
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
            wanted = {
                "dataset": {
                    k: v for k, v in wanted["dataset"].items() if k != "columns"
                }
            }
            problems.extend(
                f"scenarios/{file_name}: step {index}: {problem}"
                for problem in _part_problems(dataset, wanted["dataset"]["members"])
            )
            columns = derived[index]["dataset"].get("columns")
            if columns is not None and dataset["columns"] != columns:
                problems.append(
                    f"scenarios/{file_name}: step {index}: columns "
                    f"{json.dumps(dataset['columns'])[:300]} are not the reading-order "
                    f"rule's order of the served columns, {json.dumps(columns)[:300]}"
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
    member's `part_label` and whose rows sum to the dataset's. A dataset of
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
    reading: Sequence[Sequence[str]] | None,
    selector_keys: Sequence[str] | None,
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
                headers = exchange["response"].get("headers", {})
                if "url" not in exchange["request"] and untraced(headers):
                    problems.append(
                        f"{label}: the API response carries no {TRACE_HEADER} "
                        "header and is not marked Cache-Control: public"
                    )
            else:
                problems.extend(
                    _check_problem_code(
                        label, exchange["response"], registry, proxy_codes
                    )
                )
            problems.extend(_check_accepted(label, exchange["response"]))
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
            reading=reading,
            selector_keys=selector_keys,
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
        if "definition" in spec and spec["definition"]["spec_id"] != spec["spec_id"]:
            return "definition_spec_id_mismatch"
        if spec["spec_id"] in seen:
            return "repeated_spec_id"
        seen.add(spec["spec_id"])
    return None


def _derived(member: Any) -> bool:
    """Whether a ref member is a derived member, which names its roots."""
    return isinstance(member, dict) and "roots" in member


def _served_by(label: str, record: dict[str, Any]) -> list[str]:
    """Where a ref example's members differ from the members its `scenario`
    serves: a `spec_id` no dataset member of it has, other `parts`, another
    `content_version` or `sentence`, or an artifact digest that is not the
    digest of a file the scenario downloads. A derived member's roots are
    checked instead, each a dataset member's `spec_id` and `content_version`,
    since no scenario serves the derived member itself. A ref with no members
    names no scenario."""
    ref = record.get("ref")
    members = ref.get("members") if isinstance(ref, dict) else None
    if not isinstance(members, list) or not members:
        return []
    name = record.get("scenario")
    directory = PACKAGE_DIR / "scenarios"
    path = directory / f"{name}.json"
    scenario = _load(path) if isinstance(name, str) and path.is_file() else None
    if not isinstance(scenario, dict):
        return [f"{label}: names no scenario that serves its members"]
    served = {
        member.get("spec_id"): member
        for outcome in read_objects(scenario, "expect")
        for member in read_objects(read_object(outcome, "dataset"), "members")
    }
    digests = set()
    for exchange in read_objects(scenario, "exchanges"):
        body_file = read_string(read_object(exchange, "response"), "body_file")
        try:
            path = body_path(directory, body_file) if body_file else None
        except ValueError:
            continue  # the scenario check reports a body file it cannot read
        if path is not None and path.is_file():
            digests.add(digest_of(path.read_bytes()))
    problems = []
    for index, member in enumerate(members):
        if _derived(member):
            problems.extend(
                f"{label}: member {index} root {position} is no member {name} serves"
                for position, root in enumerate(read_objects(member, "roots"))
                if (found := served.get(root.get("spec_id"))) is None
                or found.get("content_version") != root.get("content_version")
            )
            continue
        spec = member.get("spec") if isinstance(member, dict) else None
        found = served.get(spec.get("spec_id") if isinstance(spec, dict) else None)
        if found is None:
            problems.append(f"{label}: member {index} is no member {name} serves")
            continue
        problems.extend(
            f"{label}: member {index} {key} is not {name}'s {found.get(key)!r}"
            for key in ("parts", "content_version", "sentence")
            if member.get(key) != found.get(key)
        )
        artifact = member.get("artifact")
        digest = artifact.get("digest") if isinstance(artifact, dict) else None
        if digest not in digests:
            problems.append(
                f"{label}: member {index} digest {digest} is no file {name} downloads"
            )
    return problems


def _answered_builds(scenario: Mapping[str, Any]) -> Iterator[tuple[Any, Any]]:
    """Each build in `scenario` that answered a manifest, as the manifest and
    the `options` the build posted: a manifest's own, or for a 202 the
    manifest the poll of its operation answered done, in build order."""
    by_operation: dict[str, Any] = {}
    for exchange in read_objects(scenario, "exchanges"):
        request = read_object(exchange, "request") or {}
        body = read_object(read_object(exchange, "response"), "json") or {}
        manifest = read_object(body, "manifest") if "manifest" in body else body
        operation_id = read_string(read_object(body, "operation"), "operation_id")
        if (request.get("method"), request.get("path")) == _BUILD:
            options = (read_object(request, "json") or {}).get("options")
            if operation_id is not None:
                by_operation[operation_id] = options
        elif operation_id in by_operation and "manifest" in body:
            options = by_operation[operation_id]
        else:
            continue
        yield manifest, options


def posted_renderings(scenario: Mapping[str, Any]) -> dict[str, list[Any]]:
    """The renderings the builds in `scenario` posted as their `options`, by
    the digest of the artifact each build's manifest lists for them. Builds
    that answered the same file list each rendering in build order."""
    renderings: dict[str, list[Any]] = {}
    for manifest, options in _answered_builds(scenario):
        digest = _artifact_digest(manifest, options)
        if digest is not None:
            renderings.setdefault(digest, []).append(options)
    return renderings


def _spec_renderings(scenario: Mapping[str, Any]) -> dict[str, list[Any]]:
    """The renderings the builds in `scenario` posted as their `options`, by
    the spec_id each build's manifest answered, in build order."""
    renderings: dict[str, list[Any]] = {}
    for manifest, options in _answered_builds(scenario):
        spec_id = read_string(read_object(manifest, "definition"), "spec_id")
        if spec_id is not None:
            renderings.setdefault(spec_id, []).append(options)
    return renderings


def _artifact_digest(manifest: Any, options: Any) -> str | None:
    """The digest of the artifact `manifest` lists for the posted `options`,
    or None when either does not read."""
    if not isinstance(options, dict) or not {"format", "bundle"} <= options.keys():
        return None
    artifacts = read_objects(manifest, "artifacts")
    artifact = pick_artifact({"artifacts": artifacts}, options)
    return read_string(artifact, "artifact_digest")


def _root_rendering(posted: Any, rendering: Any) -> Any:
    """The rendering a derived member's root records for a build that posted
    `posted`: DFO reads an analysis root in the frame layout, and a root read
    summed also records the `groupby` DFO rendered it with, which no recorded
    build posts."""
    if not isinstance(posted, dict):
        return posted
    wanted = {**posted, "layout": "frame"}
    if isinstance(rendering, dict) and "groupby" in rendering:
        wanted.setdefault("groupby", rendering["groupby"])
    return wanted


def _posted_by(label: str, record: dict[str, Any]) -> list[str]:
    """Where a ref example's member records a rendering other than the one
    the build that answered its file in its `scenario` posted, since a ref
    records the rendering its fetch posted, or a derived member's root
    records one no build of its spec there posted, read as `_root_rendering`
    gives. A root's `groupby` is the schema's alone to check."""
    ref = record.get("ref")
    members = ref.get("members") if isinstance(ref, dict) else None
    name = record.get("scenario")
    path = PACKAGE_DIR / "scenarios" / f"{name}.json"
    if not isinstance(members, list) or not isinstance(name, str) or not path.is_file():
        return []  # `_served_by` reports a ref that names no scenario
    scenario = _load(path)
    posted = posted_renderings(scenario)
    by_spec = _spec_renderings(scenario)
    problems = []
    for index, member in enumerate(members):
        if _derived(member):
            for position, root in enumerate(read_objects(member, "roots")):
                where = f"member {index} root {position}"
                spec_id = read_string(root, "spec_id")
                rendering = read_object(root, "rendering")
                if spec_id not in by_spec:
                    problems.append(
                        f"{label}: {where}'s spec is built by no {name} build"
                    )
                    continue
                wanted = [_root_rendering(o, rendering) for o in by_spec[spec_id]]
                if rendering not in wanted:
                    problems.append(
                        f"{label}: {where} records rendering {json.dumps(rendering)}, "
                        f"but {name}'s builds give "
                        f"{' and '.join(map(json.dumps, wanted))}"
                    )
            continue
        artifact = read_object(member, "artifact")
        digest = read_string(artifact, "digest")
        rendering = read_object(artifact, "rendering")
        if digest not in posted:
            problems.append(
                f"{label}: member {index}'s file is answered by no {name} build"
            )
        elif rendering not in posted[digest]:
            problems.append(
                f"{label}: member {index} records rendering {json.dumps(rendering)}, "
                f"but {name} posted {' and '.join(map(json.dumps, posted[digest]))}"
            )
    return problems


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
        problems.extend(_served_by(label, record))
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
        problems.extend(_posted_by(label, record))
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


# --- derived identities -----------------------------------------------------

# What fixes what among a derived member's identities. Two members that agree
# on every field of a key agree on every field it determines: `ocv_` hashes
# the `ops_` id, the root versions and the execution identity, and names one
# output; `rpk_` hashes the normal form, the root versions and the execution
# identities, so one replay computes one op-spec, version and output; the
# normal form, the root versions and the execution identity give one `rpk_`;
# and one file holds one output in one format. `roots` holds each root's
# version with the rendering it was read with, since a root read summed
# under a groupby is another input.
DERIVED_IDENTITY_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("content_version",),
        ("spec_id", "roots", "execution_identity", "output_digest"),
    ),
    (
        ("replay_key",),
        (
            "normal_form_hash",
            "roots",
            "execution_identity",
            "spec_id",
            "content_version",
            "output_digest",
            "resolved_periods",
        ),
    ),
    (("normal_form_hash", "roots", "execution_identity"), ("replay_key",)),
    (("artifact_digest",), ("artifact_format", "output_digest")),
)


def derived_identities(member: Mapping[str, Any]) -> dict[str, str | None]:
    """The identities of a derived member that `DERIVED_IDENTITY_RULES` read,
    each as text, None where the member lacks it."""
    spec = read_object(member, "spec")
    recipe = read_object(spec, "recipe")
    artifact = read_object(member, "artifact")
    roots = [
        [
            read_string(root, "spec_id"),
            read_string(root, "content_version"),
            read_object(root, "rendering"),
        ]
        for root in read_objects(member, "roots")
    ]
    periods = member.get("resolved_periods")
    return {
        "spec_id": read_string(spec, "spec_id"),
        "normal_form_hash": read_string(recipe, "normal_form_hash"),
        "replay_key": read_string(recipe, "replay_key"),
        "content_version": read_string(member, "content_version"),
        "output_digest": read_string(member, "output_digest"),
        "roots": json.dumps(roots, sort_keys=True),
        "execution_identity": read_string(member, "execution_identity"),
        "resolved_periods": None
        if periods is None
        else json.dumps(periods, sort_keys=True),
        "artifact_digest": read_string(artifact, "digest"),
        "artifact_format": read_string(read_object(artifact, "rendering"), "format"),
    }


def derived_identity_problems(
    members: Sequence[tuple[str, Mapping[str, Any]]],
) -> list[str]:
    """Where two labelled derived members agree on a key of
    `DERIVED_IDENTITY_RULES` but differ in a field it determines."""
    identities = [(label, derived_identities(member)) for label, member in members]
    problems = []
    for key, determined in DERIVED_IDENTITY_RULES:
        first: dict[tuple[str | None, ...], tuple[str, dict[str, str | None]]] = {}
        for label, facts in identities:
            values = tuple(facts[name] for name in key)
            if None in values:
                continue
            if values not in first:
                first[values] = (label, facts)
                continue
            other_label, other = first[values]
            problems.extend(
                f"{label} and {other_label} share {' and '.join(key)} "
                f"but differ in {name}"
                for name in determined
                if facts[name] != other[name]
            )
    return problems


def _check_derived_identities() -> list[str]:
    """Every derived member of a ref example the schema accepts and of the
    output-comparison vectors, checked together, since the examples name one
    computation in several places."""
    members: list[tuple[str, Mapping[str, Any]]] = []
    for path in sorted((PACKAGE_DIR / "refs").glob("*.json")):
        record = _load(path)
        if not isinstance(record, dict) or read_object(record, "error") == {
            "by": "schema"
        }:
            continue
        members.extend(
            (f"refs/{path.name} member {index}", member)
            for index, member in enumerate(
                read_objects(read_object(record, "ref"), "members")
            )
            if _derived(member)
        )
    comparison = _load(PACKAGE_DIR / "rules" / "output-comparison.json")
    for vector in read_objects(comparison, "vectors"):
        for side in ("left", "right"):
            members.extend(
                (
                    f"rules/output-comparison.json {vector.get('name')} {side} member {index}",
                    member,
                )
                for index, member in enumerate(
                    read_objects(read_object(vector, side), "members")
                )
                if _derived(member)
            )
    return derived_identity_problems(members)


# --- rules ------------------------------------------------------------------


# The frame columns that name no role (docs/frame-grammar-v1.md).
FRAME_FIXED_COLUMNS = frozenset(
    {
        "stat_var",
        "time_period",
        "value",
        "unit",
        "measurementMethod",
        "provenance",
        "status",
    }
)


def _without_recipe(unit: Any) -> Any:
    """A unit cell with `recipe` dropped from every unit-request object."""
    if not isinstance(unit, dict):
        return unit
    if "numerator" in unit or "denominator" in unit:
        return {side: _without_recipe(part) for side, part in unit.items()}
    return {name: item for name, item in unit.items() if name != "recipe"}


def output_digest_problems(label: str, vector: Mapping[str, Any]) -> list[str]:
    """Where an output-digest vector's `document` is not its frame's, read as
    JSON, or its `digest` is not the document's: the columns `stat_var`, the
    role columns, `time_period`, `value` and `unit`, each frame row once in
    those columns with no unit `recipe` and a `stat_var` of `dcid:` and an
    `ops_` id its `variables` names read as that name, digest version `out2`,
    and `out_` and 32 hex digits of the document's SHA-256. The JCS bytes
    themselves are the reference implementation's to check."""
    frame = vector["frame"]
    variables = vector.get("variables", {})
    roles = [name for name in frame["columns"] if name not in FRAME_FIXED_COLUMNS]
    columns = ["stat_var", *roles, "time_period", "value", "unit"]
    rows = []
    for row in frame["rows"]:
        cells = dict(zip(frame["columns"], row, strict=True))
        cells["unit"] = _without_recipe(cells.get("unit"))
        stat_var = cells["stat_var"]
        cells["stat_var"] = variables.get(stat_var.removeprefix("dcid:"), stat_var)
        rows.append([cells.get(name) for name in columns])
    document = json.loads(vector["document"])
    problems = []
    wanted = {"digest_version": "out2", "columns": columns}
    unmatched = list(document.get("rows", []))
    for row in rows:
        if row in unmatched:
            unmatched.remove(row)
        else:
            unmatched.append(None)
    if {key: document.get(key) for key in wanted} != wanted or unmatched:
        problems.append(
            f"{label}: vector {vector['name']!r} document is not its frame's"
        )
    hashed = "out_" + hashlib.sha256(vector["document"].encode()).hexdigest()[:32]
    if vector["digest"] != hashed:
        problems.append(
            f"{label}: vector {vector['name']!r} digest is not its document's, {hashed}"
        )
    return problems + name_document_problems(label, vector)


def name_document_problems(label: str, vector: Mapping[str, Any]) -> list[str]:
    """Where an output-digest vector's `name_documents` misname a minted
    variable: a name that is not `mv_` and 32 hex digits of its document's
    SHA-256, or a name in `variables` or in a document's slots with no
    document of its own. So a nested step's document holds its input's
    `mv_` name, which has its own document."""
    documents = vector.get("name_documents", {})
    problems = [
        f"{label}: vector {vector['name']!r} name {name} is not its document's"
        for name, text in documents.items()
        if name != "mv_" + hashlib.sha256(text.encode()).hexdigest()[:32]
    ]
    named = set(vector.get("variables", {}).values())
    for name, text in documents.items():
        try:
            document = json.loads(text)
        except ValueError:
            document = None
        slots = (
            [items for slot, items in document.items() if slot != "verb"]
            if isinstance(document, dict) and isinstance(document.get("verb"), str)
            else None
        )
        if slots is None or not all(isinstance(items, list) for items in slots):
            problems.append(
                f"{label}: vector {vector['name']!r} name {name} document is not "
                f"a verb and its slots"
            )
            continue
        named.update(
            held
            for items in slots
            for held in items
            if isinstance(held, str) and held.startswith("mv_")
        )
    problems.extend(
        f"{label}: vector {vector['name']!r} name {name} has no name document"
        for name in sorted(named - set(documents))
    )
    return problems


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    return "number" if isinstance(value, int | float) else type(value).__name__


def _unorderable(members: Sequence[Mapping[str, Any]]) -> str | None:
    """Why a reading-order vector's members cannot be ordered, or None: a
    row whose cells do not match its member's columns, a member's column
    whose cells cannot form one Arrow column, as a served Parquet file holds
    them, or a column whose cells, nulls aside, mix JSON types across the
    stacked members, which the rule gives no order."""
    types: dict[str, set[str]] = {}
    for index, member in enumerate(members):
        columns = member["columns"]
        for row in member["rows"]:
            if len(row) != len(columns):
                return (
                    f"member {index} has a row of {len(row)} cells for "
                    f"{len(columns)} columns"
                )
            for name, cell in zip(columns, row, strict=True):
                if cell is not None:
                    types.setdefault(name, set()).add(_json_type(cell))
        for position, name in enumerate(columns):
            try:
                pa.array([row[position] for row in member["rows"]])
            except pa.ArrowException as exc:
                return f"member {index} column {name!r} is no Arrow column: {exc}"
    mixed = sorted(name for name, found in types.items() if len(found) > 1)
    if mixed:
        return f"mixes JSON types in column {mixed[0]!r}"
    return None


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
        problems.extend(
            f"{label}: vector {vector['name']!r} has a window that does not "
            f"start before it ends"
            for vector in document["vectors"]
            if document["rule"] == "operation-outputs"
            and (window := vector["arguments"].get("window")) is not None
            and int(window[0]) >= int(window[1])
        )
        if document["rule"] == "output-digest":
            for vector in document["vectors"]:
                problems.extend(output_digest_problems(label, vector))
        if document["rule"] == "reading-order":
            for vector in document["vectors"]:
                unfit = _unorderable(vector["members"])
                if unfit is not None:
                    problems.append(f"{label}: vector {vector['name']!r} {unfit}")
                elif presented_rows(
                    [
                        (member.get("part"), member["columns"], member["rows"])
                        for member in vector["members"]
                    ],
                    document["reading_columns"],
                ) != (vector["columns"], vector["rows"]):
                    problems.append(
                        f"{label}: vector {vector['name']!r} orders its columns or "
                        f"rows otherwise than presented_rows"
                    )
        if document["rule"] == "file-selector":
            problems.extend(
                f"{label}: vector {vector['name']!r} gives a query other than "
                f"selector_query's"
                for vector in document["vectors"]
                if selector_query(vector["rendering"], document["query_keys"])
                != vector["query"]
            )
    return problems


def scenarios_size(directory: Path) -> int:
    """The bytes of every file in `directory`, records and body files, or 0
    when it does not exist."""
    if not directory.is_dir():
        return 0
    return sum(path.stat().st_size for path in directory.iterdir() if path.is_file())


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
            rules_schema,
        )
    )

    reading = load_reading_columns(PACKAGE_DIR)
    if reading is None:
        problems.append(
            "rules/reading-order.json: missing or unreadable, or holds no "
            "reading_columns list of column-name lists, so no dataset's columns "
            "are checked"
        )
    selector_keys = load_selector_keys(PACKAGE_DIR)
    if selector_keys is None:
        problems.append(
            "rules/file-selector.json: missing or unreadable, or holds no "
            "query_keys list of names, so no file selector request is checked"
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
            reading=reading,
            selector_keys=selector_keys,
        )
    )
    size = scenarios_size(PACKAGE_DIR / "scenarios")
    if size > SCENARIOS_BUDGET:
        problems.append(
            f"scenarios: {size:,} bytes pass the {SCENARIOS_BUDGET:,}-byte budget"
        )
    problems.extend(_check_refs(validators["dataset-ref.schema.json"]))
    problems.extend(_check_rules(validators["rules/rules.schema.json"]))
    problems.extend(_check_derived_identities())
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
