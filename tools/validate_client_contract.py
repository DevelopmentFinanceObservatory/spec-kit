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
  is named by its scenario and exists. In a recorded scenario each API request
  matches a route and method in `served/openapi.json`, its JSON body validates
  against the route's request schema, each JSON response body validates
  against the route's schema for its status, and a problem document's `code`
  is an active code registered with that status.
- Each ref example's file name matches its `name` and gets the verdict it
  states: from `dataset-ref.schema.json`, then from the rules that each
  member's definition carries its own `spec_id` and that members hold
  distinct `spec_id`s.
- Each rule file under `rules/` validates against `rules.schema.json`, names
  the rule its file name names, and names each vector once.

A recorded body is checked with one allowance: a required member whose schema
admits null may be absent, because the server leaves null members out of the
bodies it serves. It does not import `dfo_client_contract`, whose tests replay
the rule vectors.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
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


def _admits_null(openapi: dict[str, Any], schema: Any) -> bool:
    for branch in _expand(openapi, schema):
        kind = branch.get("type")
        if kind == "null" or (isinstance(kind, list) and "null" in kind):
            return True
        if branch.get("nullable") is True or branch.get("const", 0) is None:
            return True
        if None in branch.get("enum", []):
            return True
    return False


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
            if _admits_null(openapi, prop):
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
        registry = Registry().with_resource(
            _OPENAPI_URI, DRAFT202012.create_resource(openapi)
        )
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
    raw = base_path + request["path"] if "path" in request else request["origin_path"]
    path = urlsplit(raw).path
    template = routes.template(path)
    if template is None:
        return [f"{label}: {path} matches no route in served/openapi.json"]
    method = request["method"].lower()
    operation = routes.openapi["paths"][template].get(method)
    if operation is None:
        return [f"{label}: {template} serves no {request['method']}"]

    problems = []
    route = f"/paths/{_pointer_token(template)}/{method}"
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


def _check_scenarios(
    validator: jsonschema.Draft202012Validator,
    openapi: dict[str, Any],
    registry: dict[str, Any],
) -> list[str]:
    directory = PACKAGE_DIR / "scenarios"
    paths = sorted(directory.glob("*.json"))
    problems = [] if paths else ["scenarios: no scenario records found"]
    routes = _Routes(openapi)
    records: dict[str, dict[str, Any]] = {}
    named_files: set[str] = set()

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
            body_file = exchange["response"].get("body_file")
            if body_file is None:
                continue
            named_files.add(body_file)
            if not body_file.startswith(f"{name}"):
                problems.append(
                    f"scenarios/{path.name}: exchange {index}: body file "
                    f"{body_file!r} does not start with the scenario's name"
                )
            if not (directory / body_file).is_file():
                problems.append(
                    f"scenarios/{path.name}: exchange {index}: body file "
                    f"{body_file!r} is missing"
                )

        origin = record["origin"]
        if origin["kind"] != "recorded":
            continue
        base_path = urlsplit(origin["base_url"]).path.rstrip("/")
        for index, exchange in enumerate(record["exchanges"]):
            problems.extend(
                _check_exchange(
                    f"scenarios/{path.name}: exchange {index}",
                    exchange,
                    base_path,
                    routes,
                    registry,
                )
            )

    for name, record in records.items():
        origin = record["origin"]
        if origin["kind"] != "synthetic":
            continue
        parent = records.get(origin["derived_from"])
        if parent is None or parent["origin"]["kind"] != "recorded":
            problems.append(
                f"scenarios/{name}.json: derived_from {origin['derived_from']!r} "
                "is not a recorded scenario"
            )

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
        _check_scenarios(validators["scenario.schema.json"], openapi, registry)
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
