"""Tests for tools/validate_client_contract.py.

The real contract must validate, and each fault the schemas cannot catch
must be reported: a strict pointer the served document does not hold or holds
as optional or nullable, an action row out of step with the code registry, a
recorded body the served document rejects, a scenario whose steps or body
files do not fit, an outcome its exchanges and actions.json do not give, an
unredacted URL, and a ref example with the wrong verdict.

Each case validates a whole copy of the contract, the served document's
bodies included, which takes about a third of a second. The copy hard-links
the package's files, so it writes no data where the file system allows.

Run with: uv run --with jsonschema --with pytest pytest tools/tests -q
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _link_or_copy(source: str, target: str) -> None:
    """Hard-links `source` at `target`, or copies it where the two paths are
    on different file systems."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


@pytest.fixture
def package_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the real contract and registry the test may break. The
    package's files are hard links, so the copy writes no data; a test
    changes a file only through `_edit`, which replaces it, or adds, renames
    or removes files, none of which reaches the real package."""
    copy = tmp_path / "dfo_client_contract"
    shutil.copytree(validator.PACKAGE_DIR, copy, copy_function=_link_or_copy)
    registry = tmp_path / "codes.json"
    shutil.copyfile(validator.REGISTRY_PATH, registry)
    monkeypatch.setattr(validator, "PACKAGE_DIR", copy)
    monkeypatch.setattr(validator, "REGISTRY_PATH", registry)
    return copy


def _edit(path: Path, change: Callable[[Any], None]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    # A hard link written in place would change the real package's file.
    path.unlink()
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
            "error": {
                "class": "InvalidRequestError",
                "code": "refusal_without_code",
                "trace_id": response["json"]["trace_id"],
            }
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
    trace = doc["exchanges"][0]["response"]["json"]["trace_id"]
    doc["expect"] = [
        {
            "error": {
                "class": "ServerError",
                "code": "service_unavailable",
                "trace_id": trace,
            }
        }
    ]


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
_SERVER_WARNING = "scenarios/search-server-warning.json"
_FOREIGN_ARTIFACT = "scenarios/fetch-spec-foreign-artifact-url.json"
_SAME_ORIGIN_ARTIFACT = "scenarios/fetch-spec-same-origin-artifact-url.json"
_POLL_REFUSED = "scenarios/fetch-spec-poll-refused.json"
_POLL_NOT_FOUND = "scenarios/fetch-spec-poll-not-found.json"
_CUT_OFF_THEN_BUSY = "scenarios/fetch-spec-504-then-503.json"
_BUSY_THEN_CUT_OFF = "scenarios/fetch-spec-503-then-504.json"


def _recorded_origin(name: str) -> str:
    """The origin of the API the recorded scenario `name` was recorded against."""
    path = validator.PACKAGE_DIR / "scenarios" / f"{name}.json"
    base_url = json.loads(path.read_text(encoding="utf-8"))["origin"]["base_url"]
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def _artifact_urls(origin: str) -> Callable[[Any], None]:
    """The build's artifact urls become absolute on `origin`."""

    def change(doc: Any) -> None:
        for artifact in doc["exchanges"][0]["response"]["json"]["artifacts"]:
            artifact["url"] = origin + urlsplit(artifact["url"]).path

    return change


def _warned(index: int, codes: list[str]) -> Callable[[Any], None]:
    def change(doc: Any) -> None:
        answer = doc["expect"][index]["answer"]
        if codes:
            answer["warnings"] = codes
        else:
            answer.pop("warnings")

    return change


_ECHOES_SEPARATE = "scenarios/choose-echoes-separate-and-operations.json"
_PLACEHOLDER = "scenarios/choose-posts-unsupported-placeholder.json"
_CODELESS = "scenarios/search-refused-without-code.json"
_ECHO_DROPS = "scenarios/choose-echo-drops-malformed-items.json"


def _first_part(change: Callable[[Any], None]) -> Callable[[Any], None]:
    return _set_body(0, lambda body: change(body["parts"][0]))


def _first_entry(change: Callable[[Any], None]) -> Callable[[Any], None]:
    return lambda doc: change(
        doc["exchanges"][1]["request"]["json"]["input"]["entries"][0]
    )


def _second_option(**fields: Any) -> Callable[[Any], None]:
    return _first_part(lambda part: part["no_data"]["options"][1].update(fields))


def _one_part_query(part: Any) -> None:
    query = part["no_data"]["options"][0]["data_query"]
    query["parts"] = query["parts"][:1]


def _trace(trace_id: str | None, index: int = 0) -> Callable[[Any], None]:
    return _set_outcome(index, lambda o: o["error"].update(trace_id=trace_id))


def _header_trace(index: int, trace: str | None) -> Callable[[Any], None]:
    """Exchange `index` carries an x-dfo-trace-id header, and step 1 expects
    `trace` as its error's trace id."""

    def change(doc: Any) -> None:
        response = doc["exchanges"][index]["response"]
        response.setdefault("headers", {})["x-dfo-trace-id"] = "4" * 32
        doc["expect"][1]["error"]["trace_id"] = trace

    return change


def _diagnostics_trace(doc: Any) -> None:
    """Step 1's error carries the answer's diagnostics.trace_id, which no
    client reads, in place of its response's header."""
    diagnostics = doc["exchanges"][0]["response"]["json"]["diagnostics"]
    doc["expect"][1]["error"]["trace_id"] = diagnostics["trace_id"]


def _no_data_options(change: Callable[[Any], Any]) -> Callable[[Any], None]:
    """Part 0's no_data options become what `change` makes of them."""

    def edit(doc: Any) -> None:
        no_data = doc["exchanges"][0]["response"]["json"]["parts"][0]["no_data"]
        no_data["options"] = change(no_data["options"])

    return edit


def _built_destination_as_objects(doc: Any) -> None:
    """The built manifest binds destination to objects, not refs, so the
    build keeps no destination group and the sentence lookup posts none."""
    entities = doc["exchanges"][0]["response"]["json"]["definition"]["identity"][
        "entities"
    ]
    entities["destination"] = [{"dcid": ref} for ref in entities["destination"]]


def _mentions_a_string(doc: Any) -> None:
    """Each no_data option of the first answer mentions one string, its
    words joined, in place of a list of words."""
    for part in doc["exchanges"][0]["response"]["json"]["parts"]:
        for option in part.get("no_data", {}).get("options", []):
            option["mentions"] = " and ".join(option.get("mentions", []))


def _poll_waits(change: Callable[[list[float]], None]) -> Callable[[Any], None]:
    return _set_outcome(
        0, lambda o: change(o["dataset"]["long_builds"][0]["poll_waits"])
    )


def _ends_in(error: dict[str, Any], keep: int) -> Callable[[Any], None]:
    """The record keeps its first `keep` exchanges and expects `error`."""

    def change(doc: Any) -> None:
        doc["exchanges"] = doc["exchanges"][:keep]
        doc["expect"] = [{"error": error}]

    return change


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
            _set_step(1, option=1),
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
            "refs/ref-one-member.json",
            lambda doc: doc["ref"]["members"][0]["artifact"].update(
                digest="sha256:" + "0" * 64
            ),
            f"member 0 digest sha256:{'0' * 64} is no file",
        ),
        (
            "refs/ref-one-member.json",
            lambda doc: doc["ref"]["members"][0].update(
                content_version="cv_" + "0" * 32
            ),
            "member 0 content_version is not fetch-spec-known-sentence's",
        ),
        (
            "refs/ref-one-member.json",
            lambda doc: doc.update(scenario="fetch-spec-frame-layout"),
            "member 0 is no member fetch-spec-frame-layout serves",
        ),
        (
            "refs/ref-one-member.json",
            lambda doc: doc.pop("scenario"),
            "names no scenario that serves its members",
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
            '"code": "choice_invalid"',
        ),
        (
            _REQUEST_INVALID,
            _set_body(0, lambda body: body.pop("code")),
            '"code": "refusal_without_code"',
        ),
        (
            _TOP5,
            _set_body(0, _one_hint),
            '"code": "slot_defaulted"',
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
                "'artifact_expired', 'trace_id': '00000000000000000000000000000000'}} "
                "is not valid"
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
                path=doc["exchanges"][-1]["request"]["path"].partition("?")[0]
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
        (
            _SAME_ORIGIN_ARTIFACT,
            _artifact_urls("https://downloads.example.com"),
            "the steps never make exchanges 1 to 2",
        ),
        (
            _FOREIGN_ARTIFACT,
            _artifact_urls(_recorded_origin("fetch-spec-known-sentence")),
            "the steps make a GET /api/datasets/",
        ),
        (
            _SAME_ORIGIN_ARTIFACT,
            _artifact_urls("http://127.0.0.1:8083"),
            "the steps never make exchanges 1 to 2",
        ),
        (_SERVER_WARNING, _warned(0, []), "step 0 expects"),
        (_SERVER_WARNING, _warned(1, []), "step 1 expects"),
        (
            _SERVER_WARNING,
            _warned(1, ["route_deprecated", "server_warning"]),
            "step 1 expects",
        ),
        (_SERVER_WARNING, _warned(2, ["route_deprecated"]), "step 2 expects"),
        (_SERVER_WARNING, _warned(2, ["server_warning"]), "step 2 expects"),
        (_POLL_REFUSED, _repeat(4, 1), "the steps never make exchanges 7 to"),
        (
            _POLL_REFUSED,
            _set_outcome(
                0, lambda o: o["dataset"]["long_builds"][0]["poll_waits"].pop(0)
            ),
            "step 0 expects",
        ),
        (_POLL_NOT_FOUND, _repeat(1, 1), "the steps never make exchanges 2 to 2"),
        (
            _POLL_NOT_FOUND,
            _set_body(1, lambda body: body.update(retryable=True)),
            "the record lacks",
        ),
        (_CUT_OFF_THEN_BUSY, _repeat(1, 1), "the steps never make exchanges 2 to 2"),
        (
            _BUSY_THEN_CUT_OFF,
            _ends_in(
                {"class": "ServerError", "code": "timeout", "trace_id": "0" * 32}, 2
            ),
            "is a retryable refusal with retries left",
        ),
        (
            _BUSY_THEN_CUT_OFF,
            _set_outcome(0, lambda o: o["dataset"].update(retry_waits=[0.5])),
            "step 0 expects",
        ),
        (
            "client-codes.json",
            _drop_action("key_empty"),
            "no row for 'key_empty', an error the key-lookup rule raises",
        ),
        (
            "client-codes.json",
            _drop_action("profile_not_found"),
            "no row for 'profile_not_found', an error the key-lookup rule raises",
        ),
        (
            "client-codes.json",
            _drop_action("profile_without_key"),
            "no row for 'profile_without_key', an error the key-lookup rule raises",
        ),
        (
            "client-codes.json",
            _drop_action("credentials_file_invalid"),
            (
                "no row for 'credentials_file_invalid', an error the key-lookup "
                "rule raises"
            ),
        ),
        (
            "client-codes.json",
            _set_action("key_empty", **{"class": "ContractError"}),
            "takes 'ContractError', not 'AuthenticationError'",
        ),
        ("scenarios/search-no-json-body.json", _trace(None), "step 0 expects"),
        ("scenarios/fetch-spec-strict-warning.json", _trace(None), "step 0 expects"),
        (_TOP5, _header_trace(0, None), "step 1 expects"),
        (_NO_CHOICE, _trace("f" * 32, 1), "step 1 expects"),
        (_NO_CHOICE, _header_trace(0, "4" * 32), "step 1 expects"),
        (
            "scenarios/fetch-spec-digest-mismatch.json",
            _trace("f" * 32),
            "step 0 expects",
        ),
        ("scenarios/answer-timed-out.json", _trace(None, 1), "step 1 expects"),
        (
            "scenarios/choose-refused-shared-label.json",
            _trace("5" * 32, 1),
            "step 1 expects",
        ),
        (_DECLINED, _header_trace(0, None), "step 1 expects"),
        (_CODELESS, _trace("2" * 32), "step 0 expects"),
        (
            _CODELESS,
            _set_body(0, lambda body: body.pop("trace_id")),
            "step 0 expects",
        ),
        ("scenarios/search-unauthorized.json", _trace(None), "step 0 expects"),
        ("scenarios/search-proxy-unavailable.json", _trace("f" * 32), "step 0 expects"),
        ("scenarios/answer-empty-parts.json", _trace("f" * 32), "step 0 expects"),
        (_NEWER, _trace(None), "step 0 expects"),
        (
            _CODELESS,
            _set_outcome(0, lambda o: o["error"].pop("trace_id")),
            "'code': 'refusal_without_code'}} is not valid",
        ),
        (
            "scenarios/search-proxy-unavailable.json",
            lambda doc: doc["exchanges"][0]["response"]["json"].update(
                error=["transient_unavailable"]
            ),
            "proxy code ['transient_unavailable'] is not a code",
        ),
        (
            _FETCH,
            _artifact_urls("https://storage.googleapis.com"),
            "the steps never make exchanges 1 to 2",
        ),
        (
            _FETCH,
            _set_body(0, lambda body: body["definition"].pop("identity")),
            "the steps never make exchanges 1 to 2",
        ),
        (
            "scenarios/answer-definite-fetch.json",
            _first_part(lambda part: part["spec"]["definition"].pop("identity")),
            "the step follows no answer",
        ),
        (
            "scenarios/answer-definite-fetch.json",
            _first_part(lambda part: part["spec"].pop("resolved")),
            "the step follows no answer",
        ),
        (
            "scenarios/answer-nested-specs-unchecked.json",
            lambda doc: doc["exchanges"][1]["response"]["json"]["parts"][0]["spec"].pop(
                "resolved"
            ),
            "step 1 expects",
        ),
        (_SEARCH, _set_body(0, lambda body: body.update(parts=[])), "step 0 expects"),
        (
            _SEARCH,
            lambda doc: doc["exchanges"][0]["response"].update(json=[]),
            "step 0 expects",
        ),
        (
            _CODELESS,
            _set_body(0, lambda body: body.update(retryable=True)),
            "is a retryable refusal with retries left",
        ),
        (_CODELESS, _repeat(0, 1), "the steps never make exchanges 1 to 1"),
        (
            "scenarios/search-unauthorized.json",
            _repeat(0, 1),
            "the steps never make exchanges 1 to 1",
        ),
        (
            _ECHOES_SEPARATE,
            _first_entry(lambda entry: entry.pop("separate")),
            "where the rules post",
        ),
        (
            _ECHOES_SEPARATE,
            _first_entry(lambda entry: entry.update(separate=False)),
            "where the rules post",
        ),
        (
            _ECHOES_SEPARATE,
            _first_entry(lambda entry: entry.pop("operations")),
            "where the rules post",
        ),
        (
            _ECHO_DROPS,
            _posted(1, lambda entry: entry["operations"][0].update(over=3)),
            "where the rules post",
        ),
        (
            _ECHO_DROPS,
            _posted(1, lambda entry: entry["assumptions"][1].update(note="unread")),
            "where the rules post",
        ),
        (
            _ECHO_DROPS,
            _posted(1, lambda entry: entry["assumptions"][1]["refs"].append(110)),
            "where the rules post",
        ),
        (
            _ECHO_DROPS,
            _posted(0, lambda entry: entry["assumptions"].append(7)),
            "where the rules post",
        ),
        (
            _PLACEHOLDER,
            _first_entry(lambda entry: entry.update(code="scope_unsupported")),
            "where the rules post",
        ),
        (
            "scenarios/choose-refused-unknown-status.json",
            _first_part(
                lambda part: part.update(
                    status="unsupported", unsupported={"code": "scope_too_broad"}
                )
            ),
            "the record lacks",
        ),
        (
            "scenarios/choose-refused-part-without-variable-text.json",
            _first_part(lambda part: part.update(variable_text="GDP")),
            "the record lacks",
        ),
        (
            "scenarios/choose-refused-shared-label.json",
            _second_option(label="Congo [Republic]"),
            "the record lacks",
        ),
        (
            "scenarios/choose-option-two-part-query.json",
            _first_part(_one_part_query),
            "the record lacks",
        ),
        ("scenarios/answer-timed-out.json", _diagnostics_trace, "step 1 expects"),
        ("scenarios/answer-empty-parts.json", _trace(None), "step 0 expects"),
        (
            "scenarios/operation-failed-without-error.json",
            _trace(None, 1),
            "step 1 expects",
        ),
        (
            "scenarios/choose-option-two-part-query.json",
            _trace(None, 1),
            "step 1 expects",
        ),
        (
            "scenarios/fetch-spec-poll-refused-backoff.json",
            _poll_waits(lambda waits: waits.__setitem__(2, waits[1])),
            "step 0 expects",
        ),
        (
            "scenarios/fetch-spec-poll-refused-retry-after.json",
            _poll_waits(lambda waits: waits.__setitem__(1, waits[0])),
            "step 0 expects",
        ),
        (
            _FETCH,
            lambda doc: doc["exchanges"][2]["response"].update(status=206),
            "the record lacks",
        ),
        (
            _FETCH,
            lambda doc: doc["exchanges"][1]["response"].update(status=204),
            "the steps never make exchanges 2 to 2",
        ),
        (
            _FETCH,
            lambda doc: doc["exchanges"][1]["response"].update(status=201, json={}),
            "the steps never make exchanges 2 to 2",
        ),
        (
            _SEARCH,
            _no_data_options(lambda options: dict(enumerate(options))),
            "expects",
        ),
        (_SEARCH, _no_data_options(lambda options: None), "step 0 expects"),
        (_GROUP_NARROWED, _built_destination_as_objects, "where the rules post"),
        (
            "scenarios/choose-part-by-mention.json",
            _mentions_a_string,
            "the steps never make exchanges 1 to 1",
        ),
        (
            "scenarios/answer-timed-out.json",
            _set_body(0, lambda body: body.update(diagnostics="timed out")),
            "step 0 expects",
        ),
        (
            "scenarios/answer-candidates-choose-fetch.json",
            _set_body(
                0,
                lambda body: body["parts"][0]["candidates"]["specs"].__setitem__(
                    0, "x"
                ),
            ),
            "the step follows no answer",
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
        "ref-digest-stale",
        "ref-content-version-stale",
        "ref-member-not-served",
        "ref-without-scenario",
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
        "artifact-url-foreign-fetched",
        "artifact-url-same-origin-refused",
        "artifact-url-other-port-fetched",
        "server-warning-missing",
        "deprecation-pair-keyed-per-process",
        "server-warning-repeated",
        "server-warning-new-message-missing",
        "deprecation-new-pair-missing",
        "poll-retried-past-the-budget",
        "poll-wait-after-refusal-missing",
        "non-retryable-poll-polled-again",
        "poll-refusal-made-retryable",
        "build-posted-twice-after-cut-off",
        "cut-off-after-a-503-not-posted-again",
        "cut-off-retry-waits-short",
        "key-lookup-reason-key-empty-missing",
        "key-lookup-reason-profile-not-found-missing",
        "key-lookup-reason-profile-without-key-missing",
        "key-lookup-reason-credentials-file-invalid-missing",
        "key-lookup-reason-of-another-class",
        "unexpected-response-without-its-response-trace-id",
        "data-warning-without-its-response-trace-id",
        "search-warning-without-its-answer-trace-id",
        "choice-invalid-with-a-trace-id",
        "choice-invalid-takes-the-answer-trace-id",
        "storage-failure-with-a-trace-id",
        "answer-incomplete-without-its-answer-trace-id",
        "choice-invalid-takes-the-answer-response-trace-id",
        "answer-declined-without-its-answer-trace-id",
        "trace-id-of-the-header-over-the-body",
        "trace-id-header-fallback-ignored",
        "trace-id-header-fallback-dropped",
        "trace-id-on-a-refusal-with-neither",
        "trace-id-on-a-client-error",
        "version-unavailable-without-its-refusal-trace-id",
        "error-outcome-without-trace-id",
        "proxy-code-not-a-string",
        "artifact-url-on-storage-fetched",
        "manifest-without-identity-read",
        "search-spec-without-identity-read",
        "search-spec-without-resolved-read",
        "read-spec-without-resolved-beside-unread-ones",
        "empty-parts-read-as-an-answer",
        "search-body-not-an-object-read",
        "codeless-refusal-retryable-by-body-unretried",
        "codeless-refusal-not-retryable-retried",
        "unchanged-key-sent-again-after-401",
        "separate-not-echoed",
        "separate-false-echoed",
        "operations-not-echoed",
        "unread-operation-member-echoed",
        "unknown-assumption-member-echoed",
        "unread-ref-echoed",
        "unread-assumption-echoed",
        "placeholder-of-another-code",
        "unknown-status-refusal-on-a-postable-part",
        "variable-text-refusal-on-a-named-part",
        "shared-label-refusal-on-distinct-labels",
        "two-part-query-refusal-on-a-one-part-query",
        "trace-id-read-from-diagnostics",
        "body-contract-violation-without-its-trace-id",
        "failed-operation-without-its-header-trace-id",
        "option-contract-violation-without-its-answer-trace-id",
        "refused-poll-repeats-its-backoff-wait",
        "refused-poll-retry-after-ignored",
        "signed-fetch-206-read-as-the-file",
        "files-route-204-read-as-a-redirect",
        "files-route-201-read-as-a-redirect",
        "no-data-options-as-an-object-counted",
        "no-data-options-null-counted",
        "bound-entities-as-objects-compared",
        "mentions-as-a-string-matched",
        "diagnostics-not-an-object-read",
        "candidate-spec-not-an-object-read",
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


def test_a_record_that_holds_only_under_its_recorded_host_is_reported(
    package_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second replay moves the recorded origin a response names to the
    replay's own; without the move, the manifest of
    fetch-spec-same-origin-artifact-url names a host the replay is not on."""
    monkeypatch.setattr(
        validator, "relocated", lambda exchanges, *_: [dict(e) for e in exchanges]
    )
    problems = validator.validate()
    name = "scenarios/fetch-spec-same-origin-artifact-url.json: replayed under"
    assert [p for p in problems if "replayed under" in p] == [
        p for p in problems if p.startswith(name)
    ]
    assert any(p.startswith(name) for p in problems), problems


@pytest.mark.parametrize("body_trace", ["", 7], ids=["empty", "a-number"])
def test_a_body_trace_id_that_is_not_a_non_empty_string_reads_as_absent(
    package_dir: Path, body_trace: Any
) -> None:
    """A problem document's trace_id that is empty or not a string reads as
    absent, so the refusal carries its x-dfo-trace-id header."""

    def change(doc: Any) -> None:
        response = doc["exchanges"][0]["response"]
        response["json"]["trace_id"] = body_trace
        doc["expect"][0]["error"]["trace_id"] = response["headers"]["x-dfo-trace-id"]

    _edit(package_dir / _CODELESS, change)
    problems = validator.validate()
    assert not [p for p in problems if _CODELESS in p], problems


def test_one_record_that_crashes_its_derivation_is_its_own_problem(
    package_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception while deriving one record's outcomes is that record's
    problem, and every other record is still checked."""
    step = validator._Replay.step

    def crashing(self: Any, chosen: dict[str, Any]) -> dict[str, Any]:
        if chosen.get("question") == "should donors give more aid to Kenya?":
            raise TypeError("a malformed member the rules missed")
        return step(self, chosen)

    monkeypatch.setattr(validator._Replay, "step", crashing)
    _edit(package_dir / _FETCH, _set_outcome(0, lambda o: o.update(dataset={})))
    problems = validator.validate()
    assert any(p.startswith(_DECLINED) and "TypeError" in p for p in problems), problems
    assert any(p.startswith(_FETCH) for p in problems), problems


@pytest.mark.parametrize(
    ("headers", "reported"),
    [
        ({}, True),
        ({"cache-control": "no-store"}, True),
        ({"cache-control": "public, max-age=60"}, False),
    ],
    ids=["untraced", "untraced-private", "shared-cacheable"],
)
def test_a_recorded_response_without_a_trace_id_is_reported(
    package_dir: Path, headers: dict[str, str], reported: bool
) -> None:
    """The server sends x-dfo-trace-id on every API response but one marked
    Cache-Control: public, so a recorded response without it is one the
    server did not send."""

    def change(doc: Any) -> None:
        kept = doc["exchanges"][0]["response"]["headers"]
        del kept["x-dfo-trace-id"]
        kept.update(headers)

    _edit(package_dir / _FETCH, change)
    problems = validator.validate()
    found = [p for p in problems if "carries no x-dfo-trace-id" in p]
    assert bool(found) is reported, problems


def test_a_retired_code_with_a_row_is_reported(package_dir: Path) -> None:
    _edit(validator.REGISTRY_PATH, _retire("spec_not_found"))
    problems = validator.validate()
    assert any("'spec_not_found' is retired refusal" in p for p in problems), problems


def test_a_missing_body_file_is_reported(package_dir: Path) -> None:
    (package_dir / "scenarios" / "fetch-spec-known-sentence.parquet").unlink()
    problems = validator.validate()
    assert any("is missing" in p for p in problems), problems
    assert validator.main() == 1


def test_a_symlinked_body_file_is_reported(package_dir: Path, tmp_path: Path) -> None:
    body = package_dir / "scenarios" / "fetch-spec-known-sentence.parquet"
    outside = tmp_path / "outside.parquet"
    body.rename(outside)
    body.symlink_to(outside)
    problems = validator.validate()
    assert any(
        "'fetch-spec-known-sentence.parquet'" in p and "is a symlink" in p
        for p in problems
    ), problems
    assert validator.main() == 1


def test_an_orphan_body_file_is_reported(package_dir: Path) -> None:
    (package_dir / "scenarios" / "stray.parquet").write_bytes(b"")
    problems = validator.validate()
    assert any("no scenario names this body file" in p for p in problems), problems


_ASSUMED = {"kind": "place_scope", "binding_ref": "where:", "text": "every place"}


@pytest.mark.parametrize(
    ("item", "read"),
    [
        (_ASSUMED, _ASSUMED),
        ({**_ASSUMED, "refs": ["a", 1, None]}, {**_ASSUMED, "refs": ["a"]}),
        ({**_ASSUMED, "refs": "a"}, _ASSUMED),
        ({**_ASSUMED, "refs": None}, _ASSUMED),
        ({**_ASSUMED, "note": "x"}, _ASSUMED),
        ({**_ASSUMED, "text": None}, None),
        ({"kind": "place_scope", "text": "every place"}, None),
        ("assumed", None),
    ],
    ids=[
        "whole",
        "refs-string-items",
        "refs-not-a-list",
        "refs-null",
        "unknown-member",
        "text-null",
        "binding-ref-missing",
        "not-an-object",
    ],
)
def test_an_assumption_reads_as_its_schema_members(item: Any, read: Any) -> None:
    assert validator.read_assumption(item) == read


_RANK = {"verb": "rank", "phrase": "top 5 donors"}


@pytest.mark.parametrize(
    ("item", "read"),
    [
        (
            {**_RANK, "over": "place", "target_year": 2020},
            {**_RANK, "over": "place", "target_year": 2020},
        ),
        (
            {**_RANK, "over": None, "target_year": None},
            {**_RANK, "over": None, "target_year": None},
        ),
        ({**_RANK, "over": 3, "target_year": True}, _RANK),
        ({**_RANK, "target_year": 2020.0}, {**_RANK, "target_year": 2020}),
        ({**_RANK, "target_year": 2020.5}, _RANK),
        ({**_RANK, "note": "x"}, _RANK),
        ({"verb": 7, "phrase": "top 5 donors"}, None),
        ({"verb": "rank"}, None),
        (42, None),
    ],
    ids=[
        "whole",
        "optional-null",
        "optional-of-another-type",
        "target-year-a-whole-float",
        "target-year-fractional",
        "unknown-member",
        "verb-a-number",
        "phrase-missing",
        "not-an-object",
    ],
)
def test_an_operation_reads_as_its_schema_members(item: Any, read: Any) -> None:
    # JSON text tells 2020 from 2020.0, which compare equal in Python.
    assert json.dumps(validator.read_operation(item)) == json.dumps(read)


def test_an_assumptions_list_with_no_item_read_echoes_empty() -> None:
    """A list none of whose items reads still states that the reading assumed
    nothing, where a part with no list posts no assumptions."""
    spec = {"spec_id": "spec_a"}
    part = {"status": "definite", "assumptions": [7, {**_ASSUMED, "kind": 1}]}
    assert validator.reading_echo(part, spec) == []
    assert validator.reading_echo({"status": "definite"}, spec) is None


def _answer(*parts: dict[str, Any]) -> Any:
    return validator.answer_from_body({"kind": "resolution", "parts": list(parts)})


@pytest.mark.parametrize(
    ("operations", "hints"),
    [
        ([_RANK], {'Not done: rank ("top 5 donors")': "rank"}),
        (
            [{**_RANK, "phrase": "aide au Sénégal"}],
            {'Not done: rank ("aide au Sénégal")': "rank"},
        ),
        ([{**_RANK, "phrase": 5}, "rank"], {}),
    ],
    ids=["ascii-phrase", "non-ascii-phrase", "operation-not-read"],
)
def test_an_operation_not_done_is_one_hint(
    operations: list[Any], hints: dict[str, str]
) -> None:
    """The phrase is quoted as JSON with its characters kept, as both clients
    quote it, and an operation that does not read gives no hint."""
    spec = {"spec_id": "spec_a", "definition": {"spec_id": "spec_a"}}
    answer = _answer({"status": "definite", "spec": spec, "operations": operations})
    assert validator.reading_hints(answer) == hints


def _candidate(**resolved: Any) -> dict[str, Any]:
    return {
        "spec_id": "spec_a",
        "definition": {"spec_id": "spec_a"},
        "resolved": resolved,
    }


@pytest.mark.parametrize(
    "part",
    [
        {"status": "candidates", "candidates": {"specs": [_candidate()] * 2}},
        {
            "status": "no_data",
            "no_data": {"reason": "r", "options": [{"label": "a"}, {"label": "b"}]},
        },
    ],
    ids=["candidate", "no-data-option"],
)
@pytest.mark.parametrize(
    ("key", "option"),
    [(0, True), (0, False), (True, 0), (False, 0)],
    ids=["option-true", "option-false", "part-true", "part-false"],
)
def test_a_boolean_part_or_option_is_neither_an_index_nor_a_label(
    part: dict[str, Any], key: int | bool, option: int | bool
) -> None:
    """Each boolean would name a gap or option as 0 or 1 if read as an index."""
    with pytest.raises(validator.ClientError) as raised:
        validator.choice_for(_answer(part, part), {}, key, option)
    assert raised.value.code == "choice_invalid"


@pytest.mark.parametrize(
    ("value", "kind"),
    [(1, "integer"), (1.0, "integer"), (1.5, "number"), (True, "boolean")],
    ids=["integer", "whole-float", "fractional", "boolean"],
)
def test_a_number_with_no_fractional_part_is_an_integer(value: Any, kind: str) -> None:
    assert validator.json_type(value) == kind


@pytest.mark.parametrize(
    ("schema_version", "gaps"),
    [
        (1, []),
        (1.0, []),
        (1.5, ["/definition/schema_version"]),
        (True, ["/definition/schema_version"]),
    ],
    ids=["integer", "whole-float", "fractional", "boolean"],
)
def test_a_manifest_schema_version_is_an_integer(
    schema_version: Any, gaps: list[str]
) -> None:
    """The oracle's strict check and the ref schema agree on what an integer
    `schema_version` is."""
    package = validator.PACKAGE_DIR
    openapi = json.loads((package / "served" / "openapi.json").read_text("utf-8"))
    strict = json.loads((package / "strict-fields.json").read_text("utf-8"))
    record = json.loads((package / _FETCH).read_text("utf-8"))
    manifest = copy.deepcopy(record["exchanges"][0]["response"]["json"])
    manifest["definition"]["schema_version"] = schema_version
    members = validator._strict_members(strict)
    schema = {"$ref": "#/components/schemas/Manifest"}
    assert validator.strict_gaps(openapi, members, schema, manifest) == gaps

    ref = json.loads((package / "refs" / "ref-one-member.json").read_text("utf-8"))
    ref["ref"]["members"][0]["spec"]["definition"]["schema_version"] = schema_version
    ref_schema = json.loads((package / "dataset-ref.schema.json").read_text("utf-8"))
    accepted = validator.jsonschema.Draft202012Validator(ref_schema).is_valid(
        ref["ref"]
    )
    assert accepted == (not gaps)


@pytest.mark.parametrize(
    ("resolved", "label"),
    [
        (
            {
                "sentence_plan": [
                    {"segment": "heading", "text": " Aid "},
                    {"text": "to Kenya "},
                    {"segment": "assumptions", "text": "every donor"},
                ],
                "rendered_sentence": "Aid to Kenya, every donor.",
            },
            "Aid to Kenya",
        ),
        (
            {
                "sentence_plan": [{"segment": "assumptions", "text": "every donor"}],
                "rendered_sentence": "Aid to Kenya, every donor.",
            },
            "Aid to Kenya, every donor.",
        ),
        ({}, None),
    ],
    ids=["segment-less-token-in-heading", "rendered-sentence-fallback", "neither"],
)
def test_a_candidate_label_is_its_heading_or_its_sentence(
    resolved: dict[str, Any], label: str | None
) -> None:
    assert validator.candidate_label(_candidate(**resolved)) == label


_PLAIN = {"content_version": "cv_plain", "base_content_version": None}
_DERIVED = {"content_version": "cv_derived", "base_content_version": "cv_plain"}


@pytest.mark.parametrize(
    ("pages", "newest"),
    [
        ([{"versions": [_PLAIN], "next_cursor": None}], "cv_plain"),
        ([{"versions": [_DERIVED, _PLAIN], "next_cursor": None}], "cv_plain"),
        (
            [
                {"versions": [_DERIVED], "next_cursor": "c1"},
                {"versions": [_PLAIN], "next_cursor": None},
            ],
            "cv_plain",
        ),
        ([{"versions": [_DERIVED], "next_cursor": None}], None),
        ([{"versions": [], "next_cursor": None}], None),
    ],
)
def test_the_newest_version_is_the_first_plain_one_listed(
    pages: list[dict[str, Any]], newest: str | None
) -> None:
    cursors: list[str | None] = []

    def page(cursor: str | None) -> dict[str, Any]:
        cursors.append(cursor)
        return pages[len(cursors) - 1]

    assert validator.newest_plain(page) == newest
    assert cursors == [None, *(p["next_cursor"] for p in pages[:-1])]


@pytest.mark.parametrize(
    "body",
    [
        {"items": [_PLAIN], "next_cursor": None},
        {"versions": [{"content_version": "cv_plain"}], "next_cursor": None},
        {"versions": [{**_PLAIN, "base_content_version": 1}], "next_cursor": None},
        {"versions": [_DERIVED], "next_cursor": 1},
        {"versions": [_PLAIN], "next_cursor": 1},
    ],
)
def test_an_unreadable_versions_page_raises(body: dict[str, Any]) -> None:
    with pytest.raises(validator.ClientError) as raised:
        validator.newest_plain(lambda _: body)
    assert raised.value.code == "contract_violation"


def test_a_required_member_that_admits_null_stays_required() -> None:
    """A recorded body must carry a required member, null or not."""
    served = validator.PACKAGE_DIR / "served" / "openapi.json"
    routes = validator._Routes(json.loads(served.read_text(encoding="utf-8")))
    page = "/components/schemas/DatasetVersionsPage"
    body = {
        "spec_id": "spec_" + "0" * 32,
        "enrichment_stamp": "0" * 64,
        "versions": [],
        "complete": True,
    }
    assert routes.validate(page, {**body, "next_cursor": None}) == []
    problems = routes.validate(page, body)
    assert any("'next_cursor' is a required property" in p for p in problems)


def test_scenarios_past_their_budget_are_reported(
    package_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = validator.scenarios_size(package_dir / "scenarios")
    assert f"scenarios: {size:,} bytes pass" not in " ".join(validator.validate())
    monkeypatch.setattr(validator, "SCENARIOS_BUDGET", size - 1)
    assert f"scenarios: {size:,} bytes pass the {size - 1:,}-byte budget" in (
        validator.validate()
    )


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
