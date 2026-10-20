"""The accessors read the packaged contract, and a missing record set fails
loudly rather than reading as an empty suite."""

from __future__ import annotations

from pathlib import Path

import pytest

import dfo_client_contract
from dfo_client_contract import contract


def test_every_rule_file_is_listed() -> None:
    rules_dir = dfo_client_contract.contract_dir() / "rules"
    files = {p.stem for p in rules_dir.glob("*.json")} - {"rules.schema"}
    assert files == set(dfo_client_contract.RULES)


def test_the_served_document_names_its_commit() -> None:
    source = dfo_client_contract.served_source()
    assert dfo_client_contract.openapi()["openapi"].startswith("3.")
    assert len(source["commit"]) == 40


def test_records_load() -> None:
    assert dfo_client_contract.scenarios()
    assert dfo_client_contract.ref_examples()


@pytest.mark.parametrize("load", [contract.scenarios, contract.ref_examples])
def test_an_empty_record_set_is_a_hard_failure(
    load: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "refs").mkdir()
    monkeypatch.setattr(contract, "contract_dir", lambda: tmp_path)
    assert callable(load)
    with pytest.raises(RuntimeError, match="no records found"):
        load()


def test_an_unknown_rule_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown rule"):
        dfo_client_contract.rule("cache")


def test_client_codes_are_disjoint_from_the_refusal_codes() -> None:
    client = [row["code"] for row in dfo_client_contract.client_codes()["codes"]]
    refusals = {row["code"] for row in dfo_client_contract.actions()["codes"]}
    assert client == sorted(set(client))
    assert not refusals & set(client)
    assert dfo_client_contract.client_codes_schema()["title"] == "Client codes"
