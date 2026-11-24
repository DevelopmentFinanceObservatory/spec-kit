"""Tests for dfo_spec_kit.registry: the typed code-registry loader.

Exercises the packaged registry mirror through the public
CodeRegistry/CodeEntry/CodeMapping surface, plus a byte-identity check
against the canonical `registries/codes.json` at the repo root (the same
check `tools/validate_registry.py` runs in CI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dfo_spec_kit.registry import (
    FLOW_ROLE_AXES,
    LIMIT_NAMES,
    REGISTRY,
    WARNING_REASONS,
    CodeEntry,
    CodeMapping,
    _warning_reasons_from_json,
    load_registry,
)

_REASON = {
    "code": "bilateral_reading_unavailable",
    "reason": "scope_undecided",
    "definition": "A reason.",
}

# Repo root is four levels up from this file (tests/ -> spec-kit -> python ->
# repo root); the canonical registry is always present in-repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = _REPO_ROOT / "registries" / "codes.json"
_MIRROR_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "dfo_spec_kit"
    / "registry"
    / "codes.json"
)


def test_mirror_is_byte_identical_to_canonical_registry() -> None:
    """The packaged mirror must never drift from the canonical registry --
    the same invariant tools/validate_registry.py enforces in CI."""
    assert _MIRROR_PATH.read_bytes() == _CANONICAL_PATH.read_bytes()


def test_registry_loads_a_non_empty_set_of_entries() -> None:
    assert len(REGISTRY) > 0
    assert len(REGISTRY.entries) == len(REGISTRY)


def test_registry_supports_lookup_by_code() -> None:
    entry = REGISTRY["variable_not_resolved"]
    assert isinstance(entry, CodeEntry)
    assert entry.code == "variable_not_resolved"
    assert entry.classification == "refusal"
    assert "variable_not_resolved" in REGISTRY
    assert "variable_not_resolved" in REGISTRY.codes


def test_unknown_code_lookup_raises_key_error() -> None:
    assert "not_a_real_code" not in REGISTRY
    try:
        REGISTRY["not_a_real_code"]
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unregistered code")


def test_entry_mappings_expose_endpoint_door_channel() -> None:
    entry = REGISTRY["binding_mismatch"]
    assert entry.mappings
    mapping = entry.mappings[0]
    assert isinstance(mapping, CodeMapping)
    assert mapping.endpoint == "search.resolve"
    assert mapping.channel == "no_data_reason"
    assert mapping.doors == ("raw_text", "parsed", "dataset_resubmit")
    assert mapping.http_status is None


def test_permits_matches_a_registered_endpoint_door_channel() -> None:
    entry = REGISTRY["binding_mismatch"]
    assert entry.permits(
        endpoint="search.resolve", channel="no_data_reason", door="raw_text"
    )
    assert not entry.permits(
        endpoint="search.resolve", channel="no_data_reason", door="options"
    )
    assert not entry.permits(endpoint="search.options", channel="no_data_reason")


def test_permits_ignores_door_when_mapping_declares_none() -> None:
    entry = REGISTRY["date_coverage_partial"]
    mapping = entry.mappings[0]
    assert mapping.doors is None
    assert entry.permits(
        endpoint="search.resolve", channel="diagnostics_warning", door="parsed"
    )
    assert entry.permits(endpoint="search.resolve", channel="diagnostics_warning")


def test_options_endpoint_codes_carry_the_extended_vocabulary() -> None:
    entry = REGISTRY["option_target_invalid"]
    assert entry.classification == "refusal"
    mapping = entry.mappings[0]
    assert mapping.endpoint == "search.options"
    assert mapping.doors == ("options",)
    assert mapping.channel == "problem_details"
    assert mapping.http_status == 422


def test_flagged_match_gate_codes_carry_the_extended_channel() -> None:
    entry = REGISTRY["no_member_has_data_at_place"]
    assert entry.classification == "refusal"
    assert any(mapping.channel == "flagged_match_gate" for mapping in entry.mappings)


def test_load_registry_returns_an_independent_equivalent_instance() -> None:
    """load_registry() re-parses the packaged JSON on every call; a second
    call must agree with the module-level REGISTRY without being the same
    object."""
    reloaded = load_registry()
    assert reloaded is not REGISTRY
    assert reloaded.format_version == REGISTRY.format_version
    assert [e.code for e in reloaded.entries] == [e.code for e in REGISTRY.entries]


def test_entries_are_sorted_by_code() -> None:
    codes = [entry.code for entry in REGISTRY.entries]
    assert codes == sorted(codes)


@pytest.mark.parametrize(
    "name", ["flow-role-axes.json", "limit-names.json", "warning-reasons.json"]
)
def test_mirror_is_byte_identical_to_canonical_registry_file(name: str) -> None:
    assert (_MIRROR_PATH.parent / name).read_bytes() == (
        _REPO_ROOT / "registries" / name
    ).read_bytes()


def test_limit_names_are_keyed_by_name() -> None:
    entry = LIMIT_NAMES["MAX_TABLE_ROWS"]
    assert entry.name == "MAX_TABLE_ROWS"
    assert entry.owning_contract == "presentation"
    assert all(key == value.name for key, value in LIMIT_NAMES.items())


def test_flow_role_axes_place_every_role_on_one_axis() -> None:
    assert FLOW_ROLE_AXES == {
        "provider": "origin",
        "creditor": "origin",
        "source": "origin",
        "financingProvider": "origin",
        "recipient": "destination",
        "debtor": "destination",
        "target": "destination",
        "financingRecipient": "destination",
    }


def test_warning_reasons_are_keyed_by_code_then_reason() -> None:
    entry = WARNING_REASONS["bilateral_reading_unavailable"]["slot_not_served"]
    assert (entry.code, entry.reason) == (
        "bilateral_reading_unavailable",
        "slot_not_served",
    )
    assert [name for name, _ in entry.params] == ["slot"]
    assert all(
        code == value.code and reason == value.reason
        for code, reasons in WARNING_REASONS.items()
        for reason, value in reasons.items()
    )


def test_warning_reasons_are_hashable() -> None:
    entry = WARNING_REASONS["bilateral_reading_unavailable"]["slot_not_served"]
    assert hash(entry) == hash(entry)


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"format_version": 2, "entries": []}, id="format-version"),
        pytest.param(
            {"format_version": 1, "entries": [_REASON, _REASON]}, id="duplicate"
        ),
    ],
)
def test_warning_reasons_loader_refuses(data: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _warning_reasons_from_json(data)
