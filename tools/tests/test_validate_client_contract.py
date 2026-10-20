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
            lambda doc: doc["exchanges"][1]["request"].update(method="POST"),
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
            "step 1 expects",
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
            "scenarios/choose-place-beside-definite.json",
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
            "does not follow a step in ['mint']",
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
            "served/SOURCE.json",
            lambda doc: doc.update(commit="8408f1f"),
            "commit is not a 40-character sha",
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
        "source-commit-short",
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


def test_no_scenarios_is_a_hard_failure(package_dir: Path) -> None:
    for path in (package_dir / "scenarios").iterdir():
        path.unlink()
    assert "scenarios: no scenario records found" in validator.validate()
