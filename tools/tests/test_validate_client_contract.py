"""Tests for tools/validate_client_contract.py.

The real contract must validate, and each fault the schemas cannot catch
must be reported: a strict pointer the served document does not hold or holds
as optional or nullable, an action row out of step with the code registry, a
recorded body the served document rejects, a scenario whose steps or body
files do not fit, an outcome its exchanges and actions.json do not give, an
unredacted URL, and a ref example with the wrong verdict.

Each case validates a whole copy of the contract, the served document's
bodies included, which takes about a third of a second.

Run with: uv run --with jsonschema --with pytest pytest tools/tests -q
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "validate_client_contract.py"
_spec = importlib.util.spec_from_file_location("validate_client_contract", _MODULE_PATH)
validator = importlib.util.module_from_spec(_spec)
sys.modules["validate_client_contract"] = validator
_spec.loader.exec_module(validator)

_SEARCH = "scenarios/search-place-ambiguous.json"
_FETCH = "scenarios/fetch-spec-known-sentence.json"
_MIXED = "scenarios/answer-mixed-fetch-raises.json"
_FOLDED = "scenarios/answer-several-readings-folded.json"
_TOP5 = "scenarios/search-warning-top5.json"
_NO_CHOICE = "scenarios/choose-refused-no-choice.json"
_DECLINED = "scenarios/answer-declined.json"
_REQUEST_INVALID = "scenarios/fetch-spec-request-invalid.json"
_UNKNOWN_VERSION = "scenarios/fetch-ref-unknown-version.json"
_NEWER = "scenarios/fetch-ref-expired-newer-version.json"
_NOT_REPRODUCIBLE = "scenarios/fetch-ref-expired-not-reproducible.json"
_ECHOES = "scenarios/choose-candidate-echoes-assumptions.json"
_REBOUND = "scenarios/fetch-spec-entity-rebound.json"
_DROPPED = "scenarios/fetch-spec-entity-dropped.json"
_GROUP_NARROWED = "scenarios/fetch-spec-group-narrowed.json"
_GROUP_REBOUND = "scenarios/fetch-spec-group-origin-rebound.json"
_ORIGIN_GROUP = {"label": "G7", "group_id": "groups/G7"}


def _posted(index: int, change: Callable[[Any], None]) -> Callable[[Any], None]:
    """Changes the input the record's second exchange posts."""
    return lambda doc: change(
        doc["exchanges"][1]["request"]["json"]["input"]["entries"][index]
    )


@pytest.fixture
def package_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the real contract and registry the test may break."""
    copy = tmp_path / "dfo_client_contract"
    shutil.copytree(validator.PACKAGE_DIR, copy)
    registry = tmp_path / "codes.json"
    shutil.copyfile(validator.REGISTRY_PATH, registry)
    monkeypatch.setattr(validator, "PACKAGE_DIR", copy)
    monkeypatch.setattr(validator, "REGISTRY_PATH", registry)
    return copy


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_the_real_contract_is_valid(package_dir: Path) -> None:
    assert validator.validate() == []
    assert validator.main() == 0


def _strict(pointer: str) -> Callable[[Any], None]:
    return lambda doc: doc["required"].append({"pointer": pointer})


def _drop_action(code: str) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        doc["codes"] = [row for row in doc["codes"] if row["code"] != code]

    return change


def _add_action(row: dict[str, Any]) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        doc["codes"] = sorted([*doc["codes"], row], key=lambda r: r["code"])

    return change


def _set_action(code: str, **fields: Any) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        row = next(r for r in doc["codes"] if r["code"] == code)
        row.update(fields)
        for name, value in fields.items():
            if value is None:
                del row[name]

    return change


def _retire(code: str) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        next(e for e in doc["entries"] if e["code"] == code)["lifecycle"] = "retired"

    return change


def _set_body(index: int, change: Callable[[Any], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["exchanges"][index]["response"]["json"])


def _set_property(schema: str, name: str, value: Any) -> Callable[[Any], None]:
    return lambda doc: doc["components"]["schemas"][schema]["properties"].update(
        {name: value}
    )


def _set_outcome(index: int, change: Callable[[Any], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["expect"][index])


def _first_gap(**fields: Any) -> Callable[[Any], None]:
    return _set_outcome(0, lambda o: o["answer"]["gaps"][0].update(fields))


def _part_rows(change: Callable[[Any], None]) -> Callable[[Any], None]:
    return _set_outcome(1, lambda o: change(o["dataset"]))


def _set_step(index: int, **fields: Any) -> Callable[[Any], None]:
    return lambda doc: doc["call"][index].update(fields)


def _error_code(index: int, code: str | None) -> Callable[[Any], None]:
    return _set_outcome(index, lambda o: o["error"].update(code=code))


def _one_hint(body: Any) -> None:
    """Leaves the answer's one definite part a single warn warning."""
    part = body["parts"][0]
    part["operations"], part["departures"] = [], []
    del part["warnings"][1:]


def _refused_without_code(status: int) -> Callable[[Any], None]:
    """The snapshot refuses at `status` with a body that carries no code,
    and the record expects the 4xx fallback, InvalidRequestError."""

    def change(doc: Any) -> None:
        response = doc["exchanges"][0]["response"]
        response["status"] = response["json"]["status"] = status
        del response["json"]["code"]
        doc["expect"][0] = {
            "error": {"class": "InvalidRequestError", "code": "refusal_without_code"}
        }

    return change


def _resubmit_answers_built(doc: Any) -> None:
    """Search answers the identity resubmit with the built spec_id."""
    built = doc["exchanges"][0]["response"]["json"]["definition"]["spec_id"]
    spec = doc["exchanges"][1]["response"]["json"]["parts"][0]["spec"]
    spec["spec_id"] = spec["definition"]["spec_id"] = built


def _posted_entities(change: Callable[[Any], None]) -> Callable[[Any], None]:
    """Changes the entities the record's build posts."""
    return lambda doc: change(
        doc["exchanges"][0]["request"]["json"]["input"]["scope"]["entities"]
    )


def _rebound_group_kept(doc: Any) -> None:
    """The held spec groups origin as well, and the resubmit keeps that group
    though the fetch rebinds origin."""
    doc["call"][0]["spec"]["entity_groups"]["origin"] = _ORIGIN_GROUP
    entry = doc["exchanges"][1]["request"]["json"]["input"]["entries"][0]
    entry["entity_groups"]["origin"] = _ORIGIN_GROUP


def _last_response(change: Callable[[Any], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["exchanges"][-1]["response"])


_RETRY_503 = "scenarios/search-retry-after-503.json"


def _repeat(index: int, times: int) -> Callable[[Any], None]:
    """Exchange `index` answers `times` more times before the next one."""

    def change(doc: Any) -> None:
        exchange = doc["exchanges"][index]
        for _ in range(times):
            doc["exchanges"].insert(index, copy.deepcopy(exchange))

    return change


def _refused_400(doc: Any) -> None:
    """The first search is refused 400 request_invalid, then answered."""
    doc["exchanges"][0]["response"]["json"].update(
        type="/codes/request_invalid",
        title="Bad Request",
        status=400,
        code="request_invalid",
        retryable=False,
    )
    doc["exchanges"][0]["response"]["status"] = 400


def _swap_builds(doc: Any) -> None:
    """The two builds post each other's input."""
    first, second = doc["exchanges"][1]["request"], doc["exchanges"][4]["request"]
    first["json"]["input"], second["json"]["input"] = (
        second["json"]["input"],
        first["json"]["input"],
    )


def _poll_other_operation(doc: Any) -> None:
    for exchange in doc["exchanges"]:
        if exchange["request"].get("path", "").startswith("/datasets/operations/"):
            exchange["request"]["path"] = "/datasets/operations/op_" + "f" * 64


def _made_up_code(doc: Any) -> None:
    doc["exchanges"][0]["response"]["json"]["code"] = "made_up_code"
    doc["expect"][0]["error"]["code"] = "made_up_code"


def _drop_paths(prefix: str) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        for path in [p for p in doc["paths"] if p.startswith(prefix)]:
            del doc["paths"][path]

    return change


def _versions_forbidden(response: Any) -> None:
    """The versions route refuses 403 publish_not_permitted."""
    response.update(
        status=403,
        json={
            "type": "/codes/publish_not_permitted",
            "title": "Forbidden",
            "status": 403,
            "detail": "This caller may not list versions.",
            "code": "publish_not_permitted",
            "trace_id": "0" * 32,
            "capability_version": "synthetic",
            "retryable": False,
        },
    )


def _keep_exchanges(count: int) -> Callable[[Any], None]:
    return lambda doc: doc.update(exchanges=doc["exchanges"][:count])


def _never_retried(doc: Any) -> None:
    """The 503 ends the record, and the search raises it unretried."""
    doc["exchanges"] = doc["exchanges"][:1]
    doc["expect"] = [{"error": {"class": "ServerError", "code": "service_unavailable"}}]


def _proxy_code(code: str) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        for exchange in doc["exchanges"]:
            exchange["response"]["json"]["error"] = code
        doc["expect"][0]["error"]["code"] = code

    return change


def _search_posts(change: Callable[[Any], None]) -> Callable[[Any], None]:
    return lambda doc: change(doc["exchanges"][0]["request"]["json"])


def _redirect_to(url: str) -> Callable[[Any], None]:
    """fetch-spec-known-sentence's files route names `url`, and the client
    fetches it."""

    def change(doc: Any) -> None:
        doc["exchanges"][1]["response"]["headers"]["location"] = url
        doc["exchanges"][2]["request"]["url"] = url

    return change


def _posted_scope(scope: Any) -> Callable[[Any], None]:
    return lambda doc: doc["exchanges"][0]["request"]["json"]["input"].update(
        scope=scope
    )


def _without_data_query(doc: Any) -> None:
    del doc["exchanges"][0]["response"]["json"]["parts"][0]["no_data"]["options"][0][
        "data_query"
    ]


def _second_fetch_served(doc: Any) -> None:
    """The second signed fetch serves the stored file."""
    doc["exchanges"][-1]["response"] = {
        "status": 200,
        "headers": {"content-type": "application/vnd.apache.parquet"},
        "body_file": "fetch-ref.parquet",
    }


_SUNSET = "scenarios/search-sunset-warning.json"
_FOREIGN = "https://downloads.example.com/b/o?X-Goog-Signature=redacted"


@pytest.mark.parametrize(
    ("target", "change", "expected"),
    [
        (
            "strict-fields.json",
            _strict("/components/schemas/Manifest/properties/sentence"),
            "does not resolve",
        ),
        (
            "strict-fields.json",
            _strict("/components/schemas/OperationEnvelope/properties/poll"),
            "is not required in OperationEnvelope",
        ),
        (
            "strict-fields.json",
            _strict("/components/schemas/ColumnOptionModel/properties/distinct"),
            "admits null",
        ),
        (
            "strict-fields.json",
            lambda doc: doc["enums"][0]["values"].append("verdict"),
            "no longer admits ['verdict']",
        ),
        (
            "strict-fields.json",
            _strict("/components/schemas/Manifest/properties/content_version"),
            "is listed twice",
        ),
        ("actions.json", _drop_action("spec_not_found"), "'spec_not_found' has no row"),
        (
            "actions.json",
            _add_action({"code": "alternates_unevaluated", "action": "report"}),
            "outside the active refusals",
        ),
        (
            "actions.json",
            _add_action({"code": "no_such_code", "action": "report"}),
            "is not in the registry",
        ),
        (
            "actions.json",
            lambda doc: doc["codes"].reverse(),
            "codes are not sorted",
        ),
        (
            "actions.json",
            _set_action("variable_not_resolved", gap_kind=None),
            "must carry gap_kind",
        ),
        (
            "actions.json",
            _set_action("entity_ambiguous", action="ask_data_question"),
            "choose goes with choice only",
        ),
        (
            "actions.json",
            _set_action("spec_not_found", action="ask_data_question"),
            "cannot carry action 'ask_data_question'",
        ),
        (
            _FETCH,
            _set_body(0, lambda body: body.pop("content_version")),
            "'content_version' is a required property",
        ),
        (
            _SEARCH,
            _set_body(0, lambda body: body["parts"][0].update(status="maybe")),
            "response body",
        ),
        (
            _SEARCH,
            lambda doc: doc["exchanges"][0]["request"]["json"].update(extra=True),
            "request body",
        ),
        (
            _SEARCH,
            lambda doc: doc["exchanges"][0]["request"].update(path="/search/nowhere"),
            "matches no route",
        ),
        (
            _FETCH,
            lambda doc: doc["exchanges"][1]["request"].update(method="POST", json={}),
            "serves no POST",
        ),
        (
            _SEARCH,
            lambda doc: doc["exchanges"][0].update(
                response={
                    "status": 404,
                    "json": {
                        "type": "/codes/request_invalid",
                        "title": "Not found",
                        "status": 404,
                        "detail": "No route.",
                        "code": "request_invalid",
                        "trace_id": "t",
                        "capability_version": "c",
                        "retryable": False,
                    },
                }
            ),
            "not an active code registered with status 404",
        ),
        (
            "served/openapi.json",
            _set_property("Manifest", "content_version", {"title": "Content Version"}),
            "content_version admits null",
        ),
        (
            "served/openapi.json",
            _set_property(
                "ArtifactEntryModel", "url", {"anyOf": [{"type": "string"}, {}]}
            ),
            "url admits null",
        ),
        (
            "scenarios/fetch-ref-unknown-version.json",
            _set_outcome(0, lambda o: o["error"].update({"class": "ServerError"})),
            "step 0 expects",
        ),
        (
            _MIXED,
            _first_gap(kind="no_data"),
            "step 0 expects",
        ),
        (
            "scenarios/choose-place-one-part.json",
            _first_gap(options=7),
            "step 0 expects",
        ),
        (
            _MIXED,
            _set_outcome(0, lambda o: o["answer"].update(complete=True)),
            "step 0 expects",
        ),
        (
            "actions.json",
            _set_action("snapshot_not_found", action="report"),
            "scenarios/fetch-ref-unknown-version.json: step 0 expects",
        ),
        (
            "actions.json",
            _set_action("capability_unavailable", action="report"),
            "scenarios/fetch-spec-capability-unavailable.json: step 0 expects",
        ),
        (
            "actions.json",
            _set_action("variable_not_resolved", gap_kind="no_data"),
            "scenarios/answer-mixed-fetch-raises.json: step 0 expects",
        ),
        (
            _SEARCH,
            _set_body(0, lambda body: body.pop("kind")),
            "step 0 expects",
        ),
        (
            "scenarios/fetch-ref.json",
            _set_outcome(
                0,
                lambda o: o["dataset"]["members"][0].update(
                    content_version="cv_" + "0" * 32
                ),
            ),
            "step 0 expects",
        ),
        (
            "scenarios/answer-several-readings-folded.json",
            _set_outcome(1, lambda o: o["dataset"]["columns"].pop(0)),
            "the part column comes first",
        ),
        (
            _FOLDED,
            _part_rows(lambda d: d["part_rows"][0].update(part="health")),
            "part_rows names",
        ),
        (
            _FOLDED,
            _part_rows(lambda d: d.pop("part_rows")),
            "part_rows is given exactly when",
        ),
        (
            _FOLDED,
            _part_rows(lambda d: d["part_rows"][1].update(rows=1)),
            "rows do not sum",
        ),
        (
            _TOP5,
            _set_outcome(2, lambda o: o["dataset"].update(search_warning=False)),
            "step 2 expects",
        ),
        (
            _TOP5,
            _set_step(1, strict=False),
            "the steps make a POST /datasets the record lacks",
        ),
        (
            "scenarios/fetch-strict-info-notice.json",
            _set_body(
                0, lambda body: body["parts"][0]["warnings"][0].update(severity="warn")
            ),
            "the steps never make exchanges 1 to 3",
        ),
        (
            "scenarios/answer-timed-out.json",
            _first_gap(kind="not_understood"),
            "step 0 expects",
        ),
        (
            "scenarios/answer-unsupported-part.json",
            _first_gap(kind="unknown"),
            "step 0 expects",
        ),
        (
            "scenarios/answer-unknown-kind.json",
            _set_body(0, lambda body: body.update(kind="resolution")),
            "step 0 expects",
        ),
        (
            "scenarios/fetch-partial-nothing-answered.json",
            _set_outcome(1, lambda o: o["error"].update({"class": "DeclinedError"})),
            "step 1 expects",
        ),
        (
            "scenarios/choose-refused-option-out-of-range.json",
            _set_step(1, option=2),
            "the steps make a POST /search/resolve the record lacks",
        ),
        (
            "scenarios/choose-refused-mention-names-two-parts.json",
            _set_step(1, part=1),
            "the steps make a POST /search/resolve the record lacks",
        ),
        (
            "scenarios/choose-part-by-mention.json",
            _set_step(1, part="Germany"),
            "the steps never make exchanges 1 to 1",
        ),
        (
            "scenarios/choose-candidate-two-parts.json",
            _posted(1, lambda entry: entry.pop("entity_groups")),
            "where the rules post",
        ),
        (
            "scenarios/resubmit-candidates-sibling.json",
            _posted(1, lambda entry: entry["candidates"].reverse()),
            "where the rules post",
        ),
        (
            "scenarios/resubmit-candidates-sibling.json",
            _posted(1, lambda entry: entry.update(ordering="broadest_first")),
            "where the rules post",
        ),
        (
            "scenarios/resubmit-placeholder-without-restatement.json",
            _posted(1, lambda entry: entry.update(reason="entity_ambiguous")),
            "where the rules post",
        ),
        (
            "scenarios/choose-option-beside-definite.json",
            _posted(0, lambda entry: entry.update(entry_kind="parsed")),
            "where the rules post",
        ),
        (
            "scenarios/choose-place-two-parts.json",
            _posted(0, lambda entry: entry.update(entry_kind="placeholder")),
            "where the rules post",
        ),
        (
            "scenarios/fetch-spec-narrowed.json",
            lambda doc: doc["exchanges"][1]["request"]["json"]["input"]["entries"][
                0
            ].update(variable_text="ODA"),
            "where the rules post",
        ),
        (
            _GROUP_NARROWED,
            _posted(0, lambda entry: entry.pop("entity_groups")),
            "where the rules post",
        ),
        (_GROUP_REBOUND, _rebound_group_kept, "where the rules post"),
        (
            _REBOUND,
            _posted_entities(lambda entities: entities.pop("destination")),
            "where the rules post",
        ),
        (
            _DROPPED,
            _posted_entities(lambda entities: entities.update(origin=["country/DEU"])),
            "where the rules post",
        ),
        (
            _REBOUND,
            _posted_entities(
                lambda entities: entities.update(origin=["country/DEU", "country/FRA"])
            ),
            "where the rules post",
        ),
        (_REBOUND, _set_step(0, entities={}), "where the rules post"),
        (
            _FETCH,
            lambda doc: doc["call"].append({"op": "fetch"}),
            "does not follow a step in ['choose', 'fetch_answer', 'search']",
        ),
        (
            _SEARCH,
            lambda doc: doc["exchanges"].append(doc["exchanges"][0]),
            "never make exchanges 1 to 1",
        ),
        (
            _FETCH,
            lambda doc: doc["exchanges"].pop(0),
            "do not fit the steps",
        ),
        (
            "scenarios/fetch-ref.json",
            lambda doc: doc["exchanges"][1]["response"]["headers"].update(
                location="https://storage.googleapis.com/b/o?GoogleAccessId=sa&Signature=s"
            ),
            "keeps a query other than",
        ),
        (
            "scenarios/fetch-ref.json",
            lambda doc: doc["exchanges"][2]["request"].update(
                url="https://storage.googleapis.com/b/o"
            ),
            "keeps a query other than",
        ),
        (
            "scenarios/fetch-null-member.json",
            lambda doc: doc["exchanges"][3]["response"].update(
                body_file="fetch-ref.parquet"
            ),
            "neither the scenario's own nor one its parent names",
        ),
        (_SEARCH, lambda doc: doc.update(name="search-other"), "differs"),
        (
            _SEARCH,
            lambda doc: doc["call"].insert(0, {"op": "result"}),
            "does not follow a step in ['mint', 'operation']",
        ),
        (
            _SEARCH,
            lambda doc: doc["expect"].append(doc["expect"][0]),
            "1 steps but 2 outcomes",
        ),
        (
            _SEARCH,
            lambda doc: doc.update(
                origin={"kind": "synthetic", "derived_from": "absent", "edit": "None."}
            ),
            "is not a recorded scenario",
        ),
        (
            _FETCH,
            lambda doc: doc["exchanges"][2]["response"].update(body_file="other.bin"),
            "neither the scenario's own",
        ),
        (
            "refs/ref-one-member.json",
            lambda doc: doc.update(error={"by": "schema"}),
            "carries neither or both",
        ),
        (
            "refs/ref-repeated-spec-id-rejected.json",
            lambda doc: doc["ref"]["members"].pop(),
            "breaks rule None, expected 'repeated_spec_id'",
        ),
        (
            "rules/key-format.json",
            lambda doc: doc["vectors"].append(doc["vectors"][0]),
            "vector 'secret-key-passes' repeats",
        ),
        (
            "client-codes.json",
            _set_action("offline", condition=None),
            "'condition' is a required property",
        ),
        (
            "client-codes.json",
            lambda doc: doc["codes"].reverse(),
            "codes are not sorted",
        ),
        (
            "client-codes.json",
            lambda doc: doc["codes"].insert(1, doc["codes"][0]),
            "'answer_declined' is listed twice",
        ),
        (
            "client-codes.json",
            _add_action(
                {
                    "code": "spec_not_found",
                    "class": "NotFoundError",
                    "retryable": False,
                    "condition": "x",
                }
            ),
            "'spec_not_found' is also a code in actions.json",
        ),
        (
            "client-codes.json",
            _add_action(
                {
                    "code": "alternates_unevaluated",
                    "class": "ServerError",
                    "retryable": False,
                    "condition": "x",
                }
            ),
            "'alternates_unevaluated' is also a code in registries/codes.json",
        ),
        (
            "client-codes.json",
            _set_action("answer_declined", **{"class": "SearchWarning"}),
            "raises 'SearchWarning', a class no scenario names",
        ),
        (
            "client-codes.json",
            _set_action("offline", **{"class": "by_status"}),
            "where 'refusal_without_code' alone does",
        ),
        (
            "client-codes.json",
            _drop_action("answer_declined"),
            "'answer_declined' is not a client code of one class",
        ),
        (
            "client-codes.json",
            _set_action("choice_invalid", **{"class": "ContractError"}),
            "client code 'choice_invalid' raises ContractError, not InvalidRequestError",
        ),
        (
            _NO_CHOICE,
            _error_code(1, None),
            "InvalidRequestError code None is neither a refusal's code",
        ),
        (
            _DECLINED,
            _error_code(1, "choice_invalid"),
            "raises InvalidRequestError, not DeclinedError",
        ),
        (
            _NO_CHOICE,
            _error_code(1, "invalid_argument"),
            '"code": "choice_invalid"}}',
        ),
        (
            _REQUEST_INVALID,
            _set_body(0, lambda body: body.pop("code")),
            '"code": "refusal_without_code"}}',
        ),
        (
            _TOP5,
            _set_body(0, _one_hint),
            '"code": "slot_defaulted"}}',
        ),
        (
            "served/SOURCE.json",
            lambda doc: doc.update(commit="8408f1f"),
            "commit is not a 40-character sha",
        ),
        (
            _UNKNOWN_VERSION,
            _refused_without_code(408),
            '"class": "DfoTimeoutError"',
        ),
        (
            _UNKNOWN_VERSION,
            _refused_without_code(410),
            '"class": "NotFoundError"',
        ),
        (
            _NEWER,
            _set_outcome(
                0, lambda o: o["error"].update(newest_version="cv_" + "2" * 32)
            ),
            "step 0 expects",
        ),
        (
            _NEWER,
            _set_outcome(0, lambda o: o["error"].pop("newest_version")),
            (
                "expect/0: {'error': {'class': 'VersionUnavailableError', 'code': "
                "'artifact_expired'}} is not valid"
            ),
        ),
        (
            "scenarios/fetch-ref-expired-not-reproducible.json",
            _last_response(
                lambda r: r.update(
                    status=200,
                    json={"versions": [{"content_version": "cv_" + "3" * 32}]},
                )
            ),
            "step 0 expects",
        ),
        (
            "scenarios/fetch-ref-expired-rebuild-same-version.json",
            _set_body(0, lambda body: body.update(code="snapshot_not_found")),
            "the steps never make exchanges 1 to 3",
        ),
        (
            "scenarios/fetch-spec-strict-warning.json",
            _set_body(0, lambda body: body["warnings"][0].update(severity="info")),
            "the steps make a GET /api/datasets/",
        ),
        (
            "scenarios/fetch-spec-digest-mismatch.json",
            _last_response(
                lambda r: r.update(body_file="fetch-spec-known-sentence.parquet")
            ),
            "step 0 expects",
        ),
        (
            "scenarios/fetch-spec-frame-layout.json",
            lambda doc: doc["exchanges"][0]["request"]["json"]["options"].pop("layout"),
            "where the rules post",
        ),
        (
            "scenarios/fetch-spec-resubmit-other-spec.json",
            _resubmit_answers_built,
            "the steps make a GET /api/datasets/",
        ),
        (
            "scenarios/fetch-frame-several-readings.json",
            lambda doc: doc["call"][1].pop("layout"),
            "the steps make a POST /datasets the record lacks",
        ),
        (
            _ECHOES,
            _posted(1, lambda entry: entry.pop("assumptions")),
            "where the rules post",
        ),
        (
            _ECHOES,
            _posted(0, lambda entry: entry.pop("assumptions")),
            "where the rules post",
        ),
        (
            "scenarios/resubmit-candidates-sibling.json",
            _posted(1, lambda entry: entry["candidates"][0].pop("assumptions")),
            "where the rules post",
        ),
        (
            "scenarios/answer-candidates-choose-fetch.json",
            _posted(0, lambda entry: entry.pop("assumptions")),
            "where the rules post",
        ),
        (
            "scenarios/choose-candidate-assumed-reading.json",
            _posted(0, lambda entry: entry["assumptions"][0].update(text="ODA")),
            "where the rules post",
        ),
        (
            "scenarios/choose-candidate-assumed-reading.json",
            _set_body(1, lambda body: body.update(rendered_sentence="Other.")),
            "step 1 expects",
        ),
        (
            _NOT_REPRODUCIBLE,
            lambda doc: doc["exchanges"][-1]["request"].update(
                path=doc["exchanges"][-1]["request"]["path"].removesuffix("?limit=1")
            ),
            "where the steps make a GET",
        ),
        (_NOT_REPRODUCIBLE, _last_response(_versions_forbidden), "step 0 expects"),
        (_RETRY_503, _repeat(0, 2), "the steps never make exchanges"),
        (
            _RETRY_503,
            _set_body(0, lambda body: body.update(retryable=False)),
            "the steps never make exchanges",
        ),
        (_RETRY_503, _refused_400, "the steps never make exchanges"),
        (
            "scenarios/fetch-retry-after-504.json",
            _repeat(1, 1),
            "the steps never make exchanges",
        ),
        (
            "scenarios/answer-candidates-choose-fetch.json",
            lambda doc: doc["exchanges"][1]["request"].pop("json"),
            "scenarios/answer-candidates-choose-fetch.json: schema",
        ),
        (
            _REBOUND,
            lambda doc: doc["exchanges"][0]["request"].pop("json"),
            "scenarios/fetch-spec-entity-rebound.json: schema",
        ),
        (
            "scenarios/answer-several-readings-folded.json",
            _swap_builds,
            "where the rules post",
        ),
        (
            "scenarios/mint-async-build.json",
            _poll_other_operation,
            "where the steps make a GET /datasets/operations/op_",
        ),
        (
            "scenarios/fetch-spec-known-sentence.json",
            lambda doc: doc["exchanges"][2]["request"].update(
                url="https://storage.googleapis.com/other-bucket/other-object"
                "?X-Goog-Signature=redacted"
            ),
            "where the steps make a GET https://storage.googleapis.com/",
        ),
        (
            "scenarios/fetch-spec-known-sentence.json",
            lambda doc: doc["exchanges"][1]["response"]["headers"].pop("location"),
            "names no location",
        ),
        (
            "scenarios/fetch-spec-member-not-resolvable.json",
            _made_up_code,
            "problem code 'made_up_code' is not in the registry",
        ),
        (
            "scenarios/mint-async-build.json",
            lambda doc: doc["exchanges"][0]["response"].update(status=200),
            "a mint step's build answers 200; a mint expects an operation",
        ),
        (
            "served/openapi.json",
            _drop_paths("/api/graph/"),
            "no route under /api/graph",
        ),
        (
            "served/openapi.json",
            lambda doc: doc["paths"].update({"/api/skills": {}}),
            "/api/skills is under no deployed surface",
        ),
        (
            "scenarios/fetch-ref-newest-version.json",
            _set_outcome(0, lambda o: o["newest_version"].update(content_version=None)),
            "step 0 expects",
        ),
        (
            "scenarios/mint-async-build.json",
            _set_step(0, window=[2020, 2023]),
            "scenarios/mint-async-build.json: schema: call/0",
        ),
        (
            "scenarios/search-proxy-unavailable.json",
            _keep_exchanges(2),
            "is a retryable refusal with retries left",
        ),
        (_RETRY_503, _never_retried, "is a retryable refusal with retries left"),
        (
            _SEARCH,
            _search_posts(lambda body: body.update(response_options=None)),
            "where the rules post",
        ),
        (
            _SEARCH,
            _search_posts(lambda body: body.pop("schema_version")),
            "where the rules post",
        ),
        (
            "scenarios/mint-async-build.json",
            _set_step(1, timeout=0.001),
            "scenarios/mint-async-build.json: schema: call/1",
        ),
        (
            "scenarios/search-proxy-unavailable.json",
            _proxy_code("made_up_code"),
            "proxy code 'made_up_code' is not a code proxy-codes.json lists",
        ),
        (
            "scenarios/search-proxy-unavailable.json",
            _proxy_code("rate_limited"),
            "lists with status 502",
        ),
        (
            "proxy-codes.json",
            _add_action({"code": "spec_not_found", "status": 404, "condition": "x"}),
            "'spec_not_found' is also a code in registries/codes.json",
        ),
        (
            "warning-codes.json",
            _drop_action("route_deprecated"),
            "no row for 'route_deprecated'",
        ),
        (
            "warning-codes.json",
            _add_action(
                {"code": "key_too_long", "class": "DfoWarning", "condition": "x"}
            ),
            "'key_too_long' is a warning no rule issues",
        ),
        (
            "warning-codes.json",
            _add_action({"code": "offline", "class": "DfoWarning", "condition": "x"}),
            "'offline' is also a code in client-codes.json",
        ),
        (
            "scenarios/fetch-ref.json",
            lambda doc: doc["exchanges"][1]["response"]["headers"].update(
                location="http://localhost:19493/b/o?X-Goog-Signature=redacted"
            ),
            "names localhost:19493, where a record's absolute URL names",
        ),
        (
            "scenarios/fetch-ref.json",
            lambda doc: doc["exchanges"][2]["request"].update(
                url="http://127.0.0.1:4443/b/o?X-Goog-Signature=redacted"
            ),
            "names 127.0.0.1:4443, where a record's absolute URL names",
        ),
        (_FETCH, _redirect_to(_FOREIGN), "names downloads.example.com"),
        (_FETCH, _redirect_to(_FOREIGN), "the steps never make exchanges 2 to 2"),
        (
            "scenarios/fetch-spec-relative-location.json",
            lambda doc: doc["exchanges"][2]["request"].update(origin_path="/bytes"),
            "where the steps make a GET /api/datasets/",
        ),
        (
            "scenarios/fetch-spec-empty-narrowing.json",
            _posted_scope({"facets": {}}),
            "where the rules post",
        ),
        (
            "scenarios/fetch-spec-empty-narrowing.json",
            _posted_scope({"entities": {}}),
            "where the rules post",
        ),
        (
            _SEARCH,
            lambda doc: doc["exchanges"][0]["response"].pop("json"),
            "step 0 expects",
        ),
        (
            _SEARCH,
            lambda doc: doc["origin"].update(dfo_commit="f" * 40),
            "is not served/SOURCE.json's commit",
        ),
        (
            "scenarios/fetch-ref-signed-url-refused-twice.json",
            _second_fetch_served,
            "step 0 expects",
        ),
        (
            "scenarios/operation-failed-without-error.json",
            _error_code(1, "refusal_without_code"),
            "step 1 expects",
        ),
        (
            "client-codes.json",
            _set_action("offline", retryable=None),
            "'retryable' is a required property",
        ),
        (
            "client-codes.json",
            _set_action("offline", retryable="by_status"),
            "'offline' takes its retryable flag by_status",
        ),
        (
            "scenarios/newest-version-unreadable-page.json",
            lambda doc: doc["expect"].__setitem__(
                0, {"newest_version": {"content_version": None}}
            ),
            "step 0 expects",
        ),
        (
            _RETRY_503,
            _set_outcome(0, lambda o: o["answer"].update(retry_waits=[0.5])),
            "step 0 expects",
        ),
        (
            "scenarios/fetch-spec-504-retry-after.json",
            _set_outcome(0, lambda o: o["dataset"].update(retry_waits=[0.5])),
            "step 0 expects",
        ),
        (
            _SUNSET,
            _set_outcome(0, lambda o: o["answer"].pop("warnings")),
            "step 0 expects",
        ),
        (
            _SUNSET,
            _set_outcome(
                1, lambda o: o["answer"].update(warnings=["route_deprecated"])
            ),
            "step 1 expects",
        ),
        (
            "scenarios/answer-unknown-part-status.json",
            _set_body(0, lambda body: body["parts"][1].update(status=7)),
            "the step follows no answer",
        ),
        (
            "scenarios/answer-definite-fetch.json",
            _set_body(
                0,
                lambda body: body["parts"][0]["spec"]["definition"].update(
                    spec_id="spec_" + "0" * 32
                ),
            ),
            "the step follows no answer",
        ),
        (
            "scenarios/choose-place-one-part.json",
            _without_data_query,
            "the steps never make exchanges 1 to 1",
        ),
        (
            "scenarios/fetch-spec-member-not-resolvable.json",
            lambda doc: doc["exchanges"][0]["response"]["headers"].update(
                {"content-type": "text/html"}
            ),
            "step 0 expects",
        ),
    ],
    ids=[
        "strict-pointer-unresolved",
        "strict-pointer-optional",
        "strict-pointer-nullable",
        "strict-enum-value-gone",
        "strict-pointer-repeated",
        "action-row-missing",
        "action-row-for-a-warning",
        "action-row-for-an-unknown-code",
        "actions-unsorted",
        "gap-kind-missing",
        "choose-without-choice",
        "answer-action-on-a-problem-code",
        "recorded-body-missing-a-strict-member",
        "recorded-body-unknown-part-status",
        "recorded-request-extra-member",
        "unrouted-path",
        "unserved-method",
        "problem-code-wrong-status",
        "strict-member-untyped",
        "strict-member-open-union",
        "expect-wrong-error-class",
        "expect-wrong-gap-kind",
        "expect-wrong-option-count",
        "expect-wrong-complete",
        "expect-stale-after-action-change",
        "capability-unavailable-reported",
        "expect-stale-after-gap-kind-change",
        "answer-without-kind",
        "expect-wrong-content-version",
        "stacked-without-part-column",
        "part-rows-wrong-part-value",
        "part-rows-missing",
        "part-rows-wrong-sum",
        "expect-wrong-search-warning",
        "strict-fetch-made-lenient",
        "warn-notice-raises-on-strict",
        "expect-wrong-timed-out-gap",
        "expect-wrong-unsupported-gap",
        "known-kind-raises-nothing",
        "partial-fetch-nothing-answered",
        "refusal-option-in-range",
        "refusal-part-named",
        "mention-names-no-part",
        "definite-entry-without-groups",
        "candidates-entry-reordered",
        "candidates-entry-ordering-recomputed",
        "placeholder-entry-wrong-reason",
        "definite-part-posted-parsed",
        "restated-part-posted-placeholder",
        "identity-resubmit-wrong-variable-text",
        "kept-group-dropped",
        "rebound-role-group-kept",
        "unnamed-role-dropped",
        "null-role-kept",
        "named-role-merged",
        "no-role-named-posts-entities",
        "answer-fetch-after-spec-fetch",
        "exchange-never-made",
        "exchanges-unfit",
        "location-v2-signed",
        "signed-url-without-redacted-query",
        "body-file-of-another-scenario",
        "scenario-name-mismatch",
        "step-out-of-order",
        "outcome-count-mismatch",
        "synthetic-without-a-parent",
        "body-file-misnamed",
        "ref-example-both-verdicts",
        "ref-example-wrong-verdict",
        "rule-vector-repeated",
        "client-code-without-condition",
        "client-codes-unsorted",
        "client-code-repeated",
        "client-code-is-an-action-code",
        "client-code-is-a-registry-code",
        "client-code-raises-a-warning",
        "client-code-by-status-beside-refusal-without-code",
        "client-code-the-rules-raise-missing",
        "client-code-class-changed",
        "client-error-without-code",
        "client-error-code-of-another-class",
        "client-error-code-the-rules-do-not-raise",
        "refusal-without-code",
        "search-warning-one-hint-code",
        "source-commit-short",
        "status-408-without-code",
        "status-410-without-code",
        "expect-wrong-newest-version",
        "version-unavailable-without-newest-version",
        "newest-version-from-the-versions-route",
        "unknown-version-ends-the-lookup",
        "info-warning-raises-on-strict",
        "digest-match-raises",
        "frame-build-posts-long-options",
        "resubmit-answering-another-spec",
        "several-readings-fetched-long",
        "definite-entry-without-its-assumptions",
        "chosen-entry-without-its-reading-assumptions",
        "candidate-without-its-reading-assumptions",
        "one-part-pick-without-its-reading-assumptions",
        "one-part-pick-rewords-its-reading-assumptions",
        "picked-sentence-not-taken-from-the-response",
        "versions-without-limit",
        "versions-forbidden-raises",
        "retry-past-the-budget",
        "retry-of-a-refusal-not-retryable",
        "retry-of-a-400",
        "build-504-retried-twice",
        "pick-posted-without-a-body",
        "build-posted-without-a-body",
        "builds-in-another-order",
        "poll-of-another-operation",
        "signed-url-not-the-redirect",
        "redirect-without-location",
        "synthetic-problem-code-unregistered",
        "mint-answering-200",
        "served-document-without-graph",
        "served-document-with-skills",
        "newest-version-not-the-listed-one",
        "mint-step-narrowed",
        "retry-short-of-the-budget",
        "retryable-refusal-never-retried",
        "search-posts-an-extra-member",
        "search-without-schema-version",
        "step-timeout",
        "proxy-code-unlisted",
        "proxy-code-wrong-status",
        "proxy-code-is-a-registry-code",
        "warning-code-missing",
        "warning-code-no-rule-issues",
        "warning-code-is-a-client-code",
        "location-localhost-alias",
        "signed-url-other-port",
        "location-foreign-origin-recorded",
        "location-foreign-origin-raises",
        "relative-location-against-the-origin",
        "empty-facets-posts-scope",
        "empty-entities-posts-scope",
        "search-200-without-body",
        "dfo-commit-not-the-served-commit",
        "second-signed-fetch-served",
        "failed-operation-without-error-uncoded",
        "client-code-without-retryable",
        "client-code-retryable-by-status",
        "unreadable-versions-page-reads-as-none",
        "retry-waits-backoff-over-retry-after",
        "build-retry-waits-backoff-over-retry-after",
        "sunset-warning-missing",
        "sunset-warning-repeated",
        "part-status-not-a-string",
        "definition-spec-id-mismatch",
        "one-part-option-without-data-query",
        "refusal-in-another-content-type",
    ],
)
def test_a_broken_contract_is_reported(
    package_dir: Path,
    target: str,
    change: Callable[[Any], None],
    expected: str,
) -> None:
    _edit(package_dir / target, change)
    problems = validator.validate()
    assert any(expected in problem for problem in problems), problems


def test_a_retired_code_with_a_row_is_reported(package_dir: Path) -> None:
    _edit(validator.REGISTRY_PATH, _retire("spec_not_found"))
    problems = validator.validate()
    assert any("'spec_not_found' is retired refusal" in p for p in problems), problems


def test_a_missing_body_file_is_reported(package_dir: Path) -> None:
    (package_dir / "scenarios" / "fetch-spec-known-sentence.parquet").unlink()
    problems = validator.validate()
    assert any("is missing" in p for p in problems), problems
    assert validator.main() == 1


def test_an_orphan_body_file_is_reported(package_dir: Path) -> None:
    (package_dir / "scenarios" / "stray.parquet").write_bytes(b"")
    problems = validator.validate()
    assert any("no scenario names this body file" in p for p in problems), problems


def test_a_required_member_that_admits_null_stays_required() -> None:
    """A recorded body must carry a required member, null or not."""
    served = validator.PACKAGE_DIR / "served" / "openapi.json"
    routes = validator._Routes(json.loads(served.read_text(encoding="utf-8")))
    page = "/components/schemas/DatasetVersionsPage"
    body = {"spec_id": "spec_" + "0" * 32, "versions": [], "complete": True}
    assert routes.validate(page, {**body, "next_cursor": None}) == []
    problems = routes.validate(page, body)
    assert any("'next_cursor' is a required property" in p for p in problems)


def test_no_scenarios_is_a_hard_failure(package_dir: Path) -> None:
    for path in (package_dir / "scenarios").iterdir():
        path.unlink()
    assert "scenarios: no scenario records found" in validator.validate()


_OPERATION_ID = "op_" + "a" * 64


def _write_long_build(package_dir: Path, kind: str) -> Path:
    """A synthetic record in which fetch-spec-known-sentence's build answers
    202 and two polls finish it, the first after the 202's 1.5 s pacing and
    the second after a 1 s backoff. `rate_limited` adds a 429 with
    Retry-After 3 before the first poll; `resubmitted` has the first poll
    answer a retryable failed operation and the build posted again."""
    scenarios = package_dir / "scenarios"
    parent = json.loads(
        (scenarios / "fetch-spec-known-sentence.json").read_text(encoding="utf-8")
    )
    build, files, signed = parent["exchanges"]
    operation = {
        "operation_id": _OPERATION_ID,
        "status": "pending",
        "poll": f"/api/datasets/operations/{_OPERATION_ID}",
        "retry_after_ms": 1500,
    }
    poll = {"method": "GET", "path": f"/datasets/operations/{_OPERATION_ID}"}

    def polled(status: str, **members: Any) -> dict[str, Any]:
        resource = {
            "operation": {**operation, "status": status, "retry_after_ms": None}
        }
        return {
            "request": poll,
            "response": {"status": 200, "json": resource | members},
        }

    started = {
        "request": build["request"],
        "response": {"status": 202, "json": {"operation": operation}},
    }
    polls = [polled("running"), polled("done", manifest=build["response"]["json"])]
    waits = [1.5, 1.0]
    resubmissions = 0
    if kind == "rate_limited":
        polls.insert(
            0,
            {
                "request": poll,
                "response": {"status": 429, "headers": {"retry-after": "3"}},
            },
        )
        waits = [1.5, 3.0, 1.0]
    elif kind == "resubmitted":
        error = {
            "type": "/codes/operation_failed",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "The build's worker stopped.",
            "code": "operation_failed",
            "trace_id": "0" * 32,
            "capability_version": "synthetic",
            "retryable": True,
        }
        polls[:0] = [polled("failed", error=error), started]
        waits = [1.5, 1.5, 1.0]
        resubmissions = 1
    outcome = copy.deepcopy(parent["expect"][0])
    outcome["dataset"]["long_builds"] = [
        {
            "operation_id": _OPERATION_ID,
            "poll_waits": waits,
            "resubmissions": resubmissions,
        }
    ]
    record = {
        "format_version": 1,
        "name": "fetch-spec-long-build",
        "description": "The parent's build runs on as an operation.",
        "origin": {
            "kind": "synthetic",
            "derived_from": "fetch-spec-known-sentence",
            "edit": "The build answers 202, and polls finish it.",
        },
        "call": parent["call"],
        "exchanges": [started, *polls, files, signed],
        "expect": [outcome],
    }
    path = scenarios / "fetch-spec-long-build.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _long_builds(change: Callable[[Any], None]) -> Callable[[Any], None]:
    return _set_outcome(0, lambda o: change(o["dataset"]["long_builds"][0]))


@pytest.mark.parametrize("kind", ["plain", "rate_limited", "resubmitted"])
def test_a_long_build_record_is_valid(package_dir: Path, kind: str) -> None:
    _write_long_build(package_dir, kind)
    assert validator.validate() == []


@pytest.mark.parametrize(
    ("kind", "change", "expected"),
    [
        (
            "plain",
            _long_builds(lambda b: b.update(poll_waits=[1.5, 2.0])),
            "step 0 expects",
        ),
        (
            "plain",
            _long_builds(lambda b: b.update(operation_id="op_" + "b" * 64)),
            "step 0 expects",
        ),
        (
            "plain",
            _set_outcome(0, lambda o: o["dataset"].pop("long_builds")),
            "step 0 expects",
        ),
        (
            "rate_limited",
            _long_builds(lambda b: b.update(poll_waits=[1.5, 1.5, 1.0])),
            "step 0 expects",
        ),
        (
            "resubmitted",
            _long_builds(lambda b: b.update(resubmissions=0)),
            "step 0 expects",
        ),
        (
            "resubmitted",
            _set_body(1, lambda body: body["error"].update(retryable=False)),
            "the steps never make exchanges 2 to 6",
        ),
    ],
    ids=[
        "poll-waits-without-backoff",
        "long-build-other-operation",
        "long-build-unnamed",
        "rate-limited-poll-without-its-wait",
        "resubmission-uncounted",
        "failed-operation-not-retryable",
    ],
)
def test_a_broken_long_build_is_reported(
    package_dir: Path, kind: str, change: Callable[[Any], None], expected: str
) -> None:
    _edit(_write_long_build(package_dir, kind), change)
    problems = validator.validate()
    assert any(expected in problem for problem in problems), problems
