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
- Each scenario's file name matches its `name`, names are unique, `expect`
  holds one outcome per step, a step that acts on an earlier result follows
  one, a synthetic scenario derives from a recorded one, and every body file
  exists and is the scenario's own or, for a synthetic scenario, one its
  parent names. In a recorded scenario each API request matches a route and
  method in `served/openapi.json`, its JSON body validates against the
  route's request schema, each JSON response body validates against the
  route's schema for its status, and a problem document's `code` is an active
  code registered with that status.
- Each scenario's `expect` equals the outcomes its steps reach over its
  exchanges, with error classes and gap kinds from `actions.json`, as
  `expected_outcomes` derives them; a dataset's rows and columns are left to
  the Parquet bodies. Every absolute URL a record holds, a `url` request or a
  `location` header, has the query `X-Goog-Signature=redacted`.
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

import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
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
_ACTS_ON_PREVIOUS = {"choose": {"search", "choose"}, "result": {"mint"}}
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
                f"{label}: problem code {body['code']!r} is not an active code "
                f"registered with status {response['status']}"
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
    422: "InvalidRequestError",
    429: "RateLimitError",
}


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


def error_outcome(error: str, code: str | None = None) -> dict[str, Any]:
    return {"error": {"class": error, "code": code}}


@dataclass(frozen=True, slots=True)
class Answer:
    """A search answer as a client holds it: the response body, its parts,
    sentence and status, which a local choice replaces."""

    body: dict[str, Any]
    parts: list[dict[str, Any]]
    sentence: str | None
    status: str | None


def answer_from_body(body: dict[str, Any]) -> Answer:
    return Answer(
        body, body.get("parts", []), body.get("rendered_sentence"), body.get("status")
    )


def candidate_answer(answer: Answer, part: int, candidate: int) -> Answer:
    """The answer a choice among one part's candidates makes with no request:
    that part definite on the chosen spec, with that spec's sentence."""
    chosen = answer.parts[part]
    spec = chosen["candidates"]["specs"][candidate]
    definite = {
        "status": "definite",
        "variable_text": chosen["variable_text"],
        "spec": spec,
    }
    return Answer(
        answer.body, [definite], spec["resolved"]["rendered_sentence"], "definite"
    )


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


def dataset_summary(
    members: Sequence[Mapping[str, Any]], missing: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """A dataset outcome's members and gaps, without the rows and columns the
    Parquet bodies hold."""
    return {
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


class _StepEndedError(Exception):
    """A step ends in `outcome`, an error a client raises."""

    def __init__(self, outcome: dict[str, Any]) -> None:
        super().__init__(outcome)
        self.outcome = outcome


class _UnfitError(Exception):
    """The exchanges do not fit the steps, so no outcome follows from them."""


_SEARCH = ("POST", "/search/resolve")
_BUILD = ("POST", "/datasets")
_POLL = ("GET", "/datasets/operations/")
_MANIFEST = {"$ref": "#/components/schemas/Manifest"}


@dataclass
class _Replay:
    """A record's steps run over its exchanges the way a client makes them,
    each giving the outcome it reaches."""

    exchanges: list[dict[str, Any]]
    routes: _Routes
    base_path: str
    members: Mapping[str, Sequence[str]]
    actions: Mapping[str, Mapping[str, Any]]
    cursor: int = 0
    answer: Answer | None = None
    minted: tuple[dict[str, Any], dict[str, Any]] | None = None

    @property
    def kinds(self) -> dict[str, str]:
        return gap_kinds(self.actions)

    # -- exchanges --

    def call(self, method: str, target: str) -> dict[str, Any]:
        """The exchange a client acts on for its next request: the last of a
        run that repeats one request, each refusal before it a retry.
        `target` is a path under the base URL, a path prefix ending in `/`,
        `origin` for a root-relative URL or `url` for an absolute one."""
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
            exchange["response"]["status"] >= 400
            and self.cursor < len(self.exchanges)
            and self.exchanges[self.cursor]["request"] == request
        ):
            exchange = self.exchanges[self.cursor]
            self.cursor += 1
        return exchange

    def acted_on(self, exchange: dict[str, Any]) -> Any:
        """The JSON body a client acts on. A refusal raises its error, and a
        body without a strict member raises ContractError."""
        response = exchange["response"]
        body = response.get("json")
        if response["status"] >= 400:
            code = refusal_code(body)
            error = error_class(response["status"], code, self.actions)
            raise _StepEndedError(error_outcome(error, code))
        schema = self.routes.response_schema(
            self.base_path, exchange["request"], response["status"]
        )
        self.strict(schema, body)
        return body

    def strict(self, schema: Any, body: Any) -> None:
        if strict_gaps(self.routes.openapi, self.members, schema, body):
            raise _StepEndedError(error_outcome("ContractError"))

    def download(self) -> None:
        """The files route, then its signed URL; a refused signed URL asks the
        files route again."""
        while True:
            self.acted_on(self.call("GET", "origin"))
            if self.call("GET", "url")["response"]["status"] < 400:
                return

    def resolve(self) -> Answer:
        return answer_from_body(self.acted_on(self.call(*_SEARCH)))

    def wait(self, operation: dict[str, Any]) -> dict[str, Any]:
        """Polls until the operation is done. The done resource's manifest is
        untyped in the served document, so it is checked as a Manifest."""
        manifest = None
        while operation["status"] != "done":
            resource = self.acted_on(self.call(*_POLL))
            operation, manifest = resource["operation"], resource.get("manifest")
        if not isinstance(manifest, dict):
            raise _StepEndedError(error_outcome("ContractError"))
        self.strict(_MANIFEST, manifest)
        return manifest

    def build(self, parts: list[str], spec: dict[str, Any]) -> dict[str, Any]:
        """One member: the build, then the member's files."""
        body = self.acted_on(self.call(*_BUILD))
        manifest = self.wait(body["operation"]) if "operation" in body else body
        return self.member(parts, spec, manifest)

    def member(
        self, parts: list[str], spec: dict[str, Any], manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """A built member: its sentence from Search when the build changed the
        spec_id or the client lacks it, then the download."""
        built = manifest["definition"]["spec_id"]
        sentence = spec.get("sentence")
        if built != spec["spec_id"] or sentence is None:
            (part,) = self.resolve().parts
            sentence = part["spec"]["resolved"]["rendered_sentence"]
        self.download()
        return {
            "parts": parts,
            "spec_id": built,
            "content_version": manifest["content_version"],
            "sentence": sentence,
        }

    # -- steps --

    def step(self, step: dict[str, Any]) -> dict[str, Any]:
        """The outcome `step` reaches."""
        op = step["op"]
        if op == "fetch":
            op = (
                "fetch_ref" if "ref" in step else "fetch_spec" if "spec" in step else op
            )
        try:
            return getattr(self, f"_{op}")(step)
        except _StepEndedError as ended:
            if op in {"search", "choose"}:
                self.answer = None
            return ended.outcome

    def _answered(self) -> Answer:
        if self.answer is None:
            raise _UnfitError("the step follows no answer")
        return self.answer

    def _search(self, step: dict[str, Any]) -> dict[str, Any]:
        self.answer = self.resolve()
        return answer_outcome(self.answer, self.kinds)

    def _choose(self, step: dict[str, Any]) -> dict[str, Any]:
        answer = self._answered()
        part, option = step.get("part"), step["option"]
        if isinstance(part, str):
            part = next(
                i for i, p in enumerate(answer.parts) if p.get("variable_text") == part
            )
        index = answer_gaps(answer, self.kinds)[0]["part"] if part is None else part
        chosen = answer.parts[index]
        if chosen.get("status") == "candidates" and len(answer.parts) == 1:
            if not isinstance(option, int):
                raise _UnfitError("candidates are chosen by index")
            self.answer = candidate_answer(answer, index, option)
        else:
            options = chosen.get("no_data", {}).get("options", [])
            if not any(option in (i, o.get("label")) for i, o in enumerate(options)):
                raise _StepEndedError(error_outcome("InvalidRequestError"))
            self.answer = self.resolve()
        return answer_outcome(self.answer, self.kinds)

    def _fetch(self, step: dict[str, Any]) -> dict[str, Any]:
        answer = self._answered()
        if answer.body.get("kind") != "resolution":
            raise _StepEndedError(error_outcome("DeclinedError"))
        gaps = answer_gaps(answer, self.kinds)
        if gaps and not step.get("partial", False):
            raise _StepEndedError(error_outcome("IncompleteAnswerError"))
        members = [
            self.build(
                parts,
                {
                    "spec_id": spec["spec_id"],
                    "sentence": spec["resolved"]["rendered_sentence"],
                },
            )
            for parts, spec in answer_members(answer)
        ]
        return {"dataset": dataset_summary(members, gaps)}

    def _fetch_spec(self, step: dict[str, Any]) -> dict[str, Any]:
        return {"dataset": dataset_summary([self.build([], step["spec"])], [])}

    def _fetch_ref(self, step: dict[str, Any]) -> dict[str, Any]:
        members = []
        for member in step["ref"]["members"]:
            spec_id, version = member["spec"]["spec_id"], member["content_version"]
            self.acted_on(self.call("GET", f"/datasets/{spec_id}/{version}"))
            self.download()
            members.append({**member, "spec_id": spec_id})
        return {"dataset": dataset_summary(members, [])}

    def _mint(self, step: dict[str, Any]) -> dict[str, Any]:
        operation = self.acted_on(self.call(*_BUILD))["operation"]
        self.minted = (step["spec"], operation)
        return {"operation": {k: operation[k] for k in ("operation_id", "status")}}

    def _result(self, step: dict[str, Any]) -> dict[str, Any]:
        if self.minted is None:
            raise _UnfitError("result follows no mint")
        spec, operation = self.minted
        member = self.member([], spec, self.wait(operation))
        return {"dataset": dataset_summary([member], [])}


def expected_outcomes(
    record: dict[str, Any],
    *,
    routes: _Routes,
    base_path: str,
    strict: dict[str, Any],
    actions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """The outcome each of `record`'s steps reaches over its exchanges, and
    over `actions` for error classes and gap kinds. A dataset outcome holds
    what `dataset_summary` gives, without rows and columns.

    Raises:
        ValueError: The exchanges do not fit the steps.
    """
    replay = _Replay(
        record["exchanges"], routes, base_path, _strict_members(strict), actions
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
    outcome's rows and columns are the Parquet bodies' and are not derived,
    except that several members put the `part` column first."""
    problems = []
    for index, (stated, wanted) in enumerate(
        zip(record["expect"], derived, strict=False)
    ):
        compared = stated
        if "dataset" in stated and "dataset" in wanted:
            dataset = stated["dataset"]
            compared = {
                "dataset": {
                    k: v for k, v in dataset.items() if k not in {"rows", "columns"}
                }
            }
            several = len(dataset["members"]) > 1
            if (dataset["columns"][:1] == ["part"]) != several:
                problems.append(
                    f"scenarios/{file_name}: step {index}: the part column comes "
                    "first exactly when the dataset has several members"
                )
        if compared != wanted:
            problems.append(
                f"scenarios/{file_name}: step {index} expects "
                f"{json.dumps(compared)[:300]}, but its exchanges give "
                f"{json.dumps(wanted)[:300]}"
            )
    return problems


def _check_scenarios(
    validator: jsonschema.Draft202012Validator,
    openapi: dict[str, Any],
    registry: dict[str, Any],
    strict: dict[str, Any],
    actions: Mapping[str, Mapping[str, Any]],
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
            needs = {"search", "choose"}
        else:
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

    problems.extend(
        _check_scenarios(
            validators["scenario.schema.json"],
            openapi,
            registry,
            strict,
            {row["code"]: row for row in actions["codes"]},
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
        f"action rows, {len(strict['required'])} required and "
        f"{len(strict['enums'])} enum pointers, "
        f"{len(list((PACKAGE_DIR / 'scenarios').glob('*.json')))} scenarios"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
