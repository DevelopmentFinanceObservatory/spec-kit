"""Tests for tools/validate_registry.py's check_immutability, specifically the
`mappings`-growth rule: mappings are matched across tags by `(endpoint,
channel)`, a matched pair may only add doors and must keep its `http_status`,
and a new key is permitted when its channel is legal for the entry's
classification. Every other field stays byte-equal to what a release tag
published, and reordering `mappings` changes nothing.

Also tests schema_legal_doors' retirement carve-out: a door dropped from the
schema's own doors enum (`spec_resubmit` is the real example) may disappear
from a tagged mapping's `doors`, or take its whole mapping with it if it was
the mapping's only door, without either counting as narrowing; a door still
in the enum may not shrink, even alongside a retired one in the same mapping.

Also tests check_entries' precondition for that matching: an entry's
`mappings` may not repeat an `(endpoint, channel)` pair.

Also tests the schema's coupling of `mappings`: doors to endpoints, and
`mcp.tools` to `mcp_refusal` in both directions.

Run with: uv run --with jsonschema --with pyarrow --with pytest pytest tools/tests -q
(importing tools/validate_registry.py imports jsonschema at module scope,
so a plain interpreter needs it on the path; `--with` provisions it for the
one invocation without a project-level dependency).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

CheckFn = Callable[[dict[str, Any]], list[str]]

_MODULE_PATH = Path(__file__).resolve().parent.parent / "validate_registry.py"
_spec = importlib.util.spec_from_file_location("validate_registry", _MODULE_PATH)
validate_registry = importlib.util.module_from_spec(_spec)
sys.modules["validate_registry"] = validate_registry
_spec.loader.exec_module(validate_registry)

_TAG = "v-test"
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas/registry/code-registry.schema.json"
)

_OLD_MAPPINGS: list[dict[str, Any]] = [
    {
        "endpoint": "search.resolve",
        "channel": "no_data_reason",
        "doors": ["raw_text", "parsed"],
    },
    {
        "endpoint": "search.options",
        "channel": "problem_details",
        "http_status": 409,
        "doors": ["options"],
    },
]


def _entry(mappings: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    entry = {
        "code": "widget_missing",
        "classification": "refusal",
        "definition": "The requested widget is not available.",
        "owning_contract": "search",
        "owner": "search-team",
        "introduced_in": "v1",
        "mappings": mappings,
        "lifecycle": "active",
    }
    entry.update(overrides)
    return entry


def _legal_doors() -> frozenset[str]:
    """The real schema's current door vocabulary (see schema_legal_doors),
    read once so tests exercise the actual enum rather than a hand-rolled
    stand-in. `spec_resubmit` is absent from it -- it was retired from
    schemas/registry/code-registry.schema.json's own doors enum -- which is
    what lets the retirement tests below use it as a realistic example.
    """
    schema = json.loads(_SCHEMA_PATH.read_text())
    return validate_registry.schema_legal_doors(schema)


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> CheckFn:
    """Run check_immutability against one fixed tagged entry, with no git
    involved: `latest_release_tag` and `registry_at_tag` are monkeypatched to
    a literal tag name and a literal old registry, so nothing shells out to
    git or reads this worktree's own history. `legal_doors` defaults to the
    real schema's current door enum.
    """

    def _run(
        new_entry: dict[str, Any],
        old_entry: dict[str, Any] | None = None,
        legal_doors: frozenset[str] | None = None,
    ) -> list[str]:
        old = old_entry if old_entry is not None else _entry(_OLD_MAPPINGS)
        monkeypatch.setattr(validate_registry, "latest_release_tag", lambda: _TAG)
        monkeypatch.setattr(
            validate_registry, "registry_at_tag", lambda tag: {"entries": [old]}
        )
        doors = legal_doors if legal_doors is not None else _legal_doors()
        _, problems = validate_registry.check_immutability([new_entry], doors)
        return problems

    return _run


def test_new_door_on_existing_mapping_passes(check: CheckFn) -> None:
    new_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["raw_text", "parsed", "dataset_resubmit"],
        },
        _OLD_MAPPINGS[1],
    ]
    assert check(_entry(new_mappings)) == []


def test_new_mapping_with_matching_channel_passes(check: CheckFn) -> None:
    new_mappings = [
        *_OLD_MAPPINGS,
        {
            "endpoint": "search.options",
            "channel": "no_data_reason",
            "doors": ["options"],
        },
    ]
    assert check(_entry(new_mappings)) == []


def test_new_mapping_with_channel_outside_classification_fails(check: CheckFn) -> None:
    new_mappings = [
        *_OLD_MAPPINGS,
        {"endpoint": "search.resolve", "channel": "diagnostics_warning"},
    ]
    problems = check(_entry(new_mappings))
    assert len(problems) == 1
    assert "diagnostics_warning" in problems[0]
    assert "not permitted" in problems[0]


@pytest.mark.parametrize(
    ("endpoint", "channel", "classification", "expected"),
    [
        ("datasets.mint", "manifest_warning", "warning", []),
        ("datasets.mint", "manifest_warning", "refusal", ["manifest_warning"]),
        ("mcp.tools", "mcp_refusal", "refusal", []),
        ("mcp.tools", "mcp_refusal", "warning", ["mcp_refusal"]),
    ],
)
def test_new_mapping_channel_suits_classification(
    check: CheckFn,
    endpoint: str,
    channel: str,
    classification: str,
    expected: list[str],
) -> None:
    new_mappings = [
        *_OLD_MAPPINGS,
        {"endpoint": endpoint, "channel": channel},
    ]
    problems = check(
        _entry(new_mappings, classification=classification),
        old_entry=_entry(_OLD_MAPPINGS, classification=classification),
    )
    for fragment in expected:
        assert any(fragment in problem for problem in problems)
    if not expected:
        assert problems == []


def test_door_removed_fails(check: CheckFn) -> None:
    new_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["raw_text"],
        },
        _OLD_MAPPINGS[1],
    ]
    problems = check(_entry(new_mappings))
    assert len(problems) == 1
    assert "dropped door(s) ['parsed']" in problems[0]


def test_retired_door_may_disappear_from_a_mapping_that_also_keeps_other_doors(
    check: CheckFn,
) -> None:
    """A door the schema no longer defines (`spec_resubmit`, retired from
    schemas/registry/code-registry.schema.json's own doors enum) may drop out
    of a matched mapping's `doors` without counting as narrowing -- the
    schema's own retirement released every mapping that named it."""
    old_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["raw_text", "parsed", "spec_resubmit"],
        },
        _OLD_MAPPINGS[1],
    ]
    new_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["raw_text", "parsed"],
        },
        _OLD_MAPPINGS[1],
    ]
    assert check(_entry(new_mappings), old_entry=_entry(old_mappings)) == []


def test_still_legal_door_may_not_shrink_even_alongside_a_retired_one(
    check: CheckFn,
) -> None:
    """Retirement excuses only the retired door itself; a still-legal door
    (`raw_text`) dropped from the same mapping is a real narrowing and still
    fails."""
    old_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["raw_text", "parsed", "spec_resubmit"],
        },
        _OLD_MAPPINGS[1],
    ]
    new_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["parsed"],
        },
        _OLD_MAPPINGS[1],
    ]
    problems = check(_entry(new_mappings), old_entry=_entry(old_mappings))
    assert len(problems) == 1
    assert "dropped door(s) ['raw_text']" in problems[0]


def test_mapping_whose_only_door_was_retired_may_be_removed(check: CheckFn) -> None:
    """A mapping whose only published door was `spec_resubmit` (e.g.
    `resolution_ambiguous`'s real registry history) may disappear entirely
    once that door retires -- under the current schema it never had a live
    door left to lose."""
    old_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "no_data_reason",
            "doors": ["spec_resubmit"],
        },
        _OLD_MAPPINGS[1],
    ]
    new_mappings = [_OLD_MAPPINGS[1]]
    assert check(_entry(new_mappings), old_entry=_entry(old_mappings)) == []


def test_channel_changed_fails_naming_removed_key(check: CheckFn) -> None:
    new_mappings = [
        {
            "endpoint": "search.resolve",
            "channel": "unsupported_code",
            "doors": ["raw_text", "parsed"],
        },
        _OLD_MAPPINGS[1],
    ]
    problems = check(_entry(new_mappings))
    assert len(problems) == 1
    assert "removed" in problems[0]
    assert "'search.resolve'" in problems[0]
    assert "'no_data_reason'" in problems[0]


def test_status_changed_fails(check: CheckFn) -> None:
    new_mappings = [
        _OLD_MAPPINGS[0],
        {**_OLD_MAPPINGS[1], "http_status": 422},
    ]
    problems = check(_entry(new_mappings))
    assert len(problems) == 1
    assert "http_status changed from 409 to 422" in problems[0]


def test_mappings_reordered_passes(check: CheckFn) -> None:
    assert check(_entry(list(reversed(_OLD_MAPPINGS)))) == []


def test_non_mapping_field_change_fails(check: CheckFn) -> None:
    problems = check(
        _entry(_OLD_MAPPINGS, definition="A different definition entirely.")
    )
    assert len(problems) == 1
    assert "other than lifecycle or mappings" in problems[0]


def test_check_entries_rejects_duplicate_mapping_key() -> None:
    entry = _entry(
        [
            {
                "endpoint": "search.resolve",
                "channel": "no_data_reason",
                "doors": ["raw_text"],
            },
            {
                "endpoint": "search.resolve",
                "channel": "no_data_reason",
                "doors": ["parsed"],
            },
        ]
    )
    problems = validate_registry.check_entries([entry])
    assert len(problems) == 1
    assert entry["code"] in problems[0]
    assert "'search.resolve'" in problems[0]
    assert "'no_data_reason'" in problems[0]


@pytest.fixture
def schema_validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text())
    return Draft202012Validator(schema["$defs"]["mapping"])


@pytest.mark.parametrize(
    "endpoint",
    [
        "presentation.write",
        "datasets.mint",
        "datasets.options",
        "platform.any",
        "skills.list",
        "skills.fetch",
        "skills.resource",
    ],
)
def test_schema_rejects_doors_on_non_search_endpoint(
    schema_validator: Draft202012Validator, endpoint: str
) -> None:
    mapping = {
        "endpoint": endpoint,
        "doors": ["raw_text"],
        "channel": "problem_details",
        "http_status": 400,
    }
    assert list(schema_validator.iter_errors(mapping))


@pytest.mark.parametrize(
    ("endpoint", "doors"),
    [
        ("search.resolve", ["options"]),
        ("search.options", ["raw_text"]),
        ("search.options", ["dataset_resubmit"]),
    ],
)
def test_schema_rejects_invalid_search_doors(
    schema_validator: Draft202012Validator, endpoint: str, doors: list[str]
) -> None:
    mapping = {
        "endpoint": endpoint,
        "doors": doors,
        "channel": "problem_details",
        "http_status": 400,
    }
    assert list(schema_validator.iter_errors(mapping))


@pytest.mark.parametrize(
    ("mapping", "valid"),
    [
        ({"endpoint": "mcp.tools", "channel": "mcp_refusal"}, True),
        (
            {
                "endpoint": "mcp.tools",
                "channel": "problem_details",
                "http_status": 400,
            },
            False,
        ),
        ({"endpoint": "mcp.tools", "channel": "no_data_reason"}, False),
        ({"endpoint": "datasets.mint", "channel": "mcp_refusal"}, False),
    ],
)
def test_schema_couples_mcp_tools_and_mcp_refusal(
    schema_validator: Draft202012Validator, mapping: dict[str, Any], valid: bool
) -> None:
    assert schema_validator.is_valid(mapping) is valid
