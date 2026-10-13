"""Envelope conformance: every resolved-payload fixture this bundle ships
must validate against `schemas/envelope.schema.json`. The envelope schema
composes the per-member component schema
(`schemas/component/<member>.schema.json`) through a `$ref` keyed by that
schema's own `$id`, so validating a payload against the envelope also
covers its `component` subtree. Its `description` references
`description.schema.json` from `dfo-spec-kit-descriptions` the same way, and
that schema references `steps.schema.json`. A member schema references
nothing outside itself, so it validates a component with the other member
schemas alone behind it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from dfo_descriptions import description_schema, schema_dir, steps_schema
from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import best_match
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from dfo_spec_kit_components import bundle_dir

_BUNDLE = bundle_dir()


def _load(relative_path: str) -> dict:
    return json.loads((_BUNDLE / relative_path).read_text(encoding="utf-8"))


_ENVELOPE_SCHEMA = _load("schemas/envelope.schema.json")
_COMPONENT_SCHEMAS = [
    _load("schemas/component/line_chart.schema.json"),
    _load("schemas/component/bar_chart.schema.json"),
    _load("schemas/component/table.schema.json"),
    _load("schemas/component/stat_card.schema.json"),
    _load("schemas/component/note_narration.schema.json"),
    _load("schemas/component/source_card.schema.json"),
]

_REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012))
    for schema in [
        _ENVELOPE_SCHEMA,
        *_COMPONENT_SCHEMAS,
        description_schema(),
        steps_schema(),
    ]
)
_VALIDATOR = Draft202012Validator(_ENVELOPE_SCHEMA, registry=_REGISTRY)
_MEMBER_REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012))
    for schema in _COMPONENT_SCHEMAS
)


def _member_validator(schema: dict) -> Draft202012Validator:
    return Draft202012Validator(schema, registry=_MEMBER_REGISTRY)


_FIXTURE_PAYLOAD_PATHS = [
    "fixtures/line_chart/payload.json",
    "fixtures/line_chart/payload_description.json",
    "fixtures/bar_chart/payload.json",
    "fixtures/bar_chart/payload_span.json",
    "fixtures/table/payload.json",
    "fixtures/table/payload_composed_unit.json",
    "fixtures/stat_card/payload.json",
    "fixtures/stat_card/payload_composed_unit.json",
    "fixtures/note_narration/payload.json",
    "fixtures/note_narration/payload_lineage.json",
    "fixtures/source_card/payload.json",
]


@pytest.mark.parametrize("relative_path", _FIXTURE_PAYLOAD_PATHS)
def test_fixture_payload_validates_against_the_envelope_schema(
    relative_path: str,
) -> None:
    payload = _load(relative_path)
    _VALIDATOR.validate(payload)


@pytest.mark.parametrize("relative_path", _FIXTURE_PAYLOAD_PATHS)
def test_fixture_payload_carries_the_bundle_release(relative_path: str) -> None:
    """A fixture payload is what this release mints, so it names this
    release."""
    assert (
        _load(relative_path)["catalog_version"]
        == _load("manifest.json")["catalog_version"]
    )


@pytest.mark.parametrize(
    ("relative_path", "expected_type"),
    [
        ("fixtures/line_chart/payload.json", "line_chart"),
        ("fixtures/line_chart/payload_description.json", "line_chart"),
        ("fixtures/bar_chart/payload.json", "bar_chart"),
        ("fixtures/bar_chart/payload_span.json", "bar_chart"),
        ("fixtures/table/payload.json", "table"),
        ("fixtures/table/payload_composed_unit.json", "table"),
        ("fixtures/stat_card/payload.json", "stat_card"),
        ("fixtures/stat_card/payload_composed_unit.json", "stat_card"),
        ("fixtures/note_narration/payload.json", "note_narration"),
        ("fixtures/note_narration/payload_lineage.json", "note_narration"),
        ("fixtures/source_card/payload.json", "source_card"),
    ],
)
def test_fixture_payload_component_type_matches_its_directory(
    relative_path: str, expected_type: str
) -> None:
    payload = _load(relative_path)
    assert payload["component"]["type"] == expected_type


_SPAN = "2018/2022"


def _set_path(payload: dict, path: tuple[str | int, ...], value: object) -> None:
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _component_error_path(payload: dict) -> tuple[str | int, ...]:
    """The envelope reports a member's failure at `component`, under its
    `oneOf` over members, so the field that failed comes from the member's
    own schema."""
    component = payload["component"]
    schema = _load(f"schemas/component/{component['type']}.schema.json")
    error = best_match(_member_validator(schema).iter_errors(component))
    return () if error is None else ("component", *error.absolute_path)


def _bar_in_period_mode() -> dict:
    payload = _load("fixtures/bar_chart/payload.json")
    payload["component"]["category_axis"] = {
        "mode": "period",
        "window": {"start": "2024", "end": "2024"},
    }
    return payload


@pytest.mark.parametrize(
    ("relative_path", "path", "period"),
    [
        ("fixtures/stat_card/payload.json", ("component", "period"), _SPAN),
        ("fixtures/table/payload.json", ("component", "rows", 0, "period"), _SPAN),
        (
            "fixtures/bar_chart/payload.json",
            ("component", "series", 0, "points", 0, "period"),
            "2018-01/2022-12",
        ),
        (
            "fixtures/bar_chart/payload.json",
            ("component", "category_axis", "period"),
            "2018-01-01/2022-12-31",
        ),
    ],
)
def test_a_span_validates_where_a_value_can_cover_several_periods(
    relative_path: str, path: tuple[str | int, ...], period: str
) -> None:
    payload = _load(relative_path)
    _set_path(payload, path, period)
    _VALIDATOR.validate(payload)  # must not raise


@pytest.mark.parametrize(
    ("relative_path", "path", "period"),
    [
        (
            "fixtures/line_chart/payload.json",
            ("component", "series", 0, "points", 0, "period"),
            _SPAN,
        ),
        (
            "fixtures/line_chart/payload.json",
            ("component", "x_axis", "window", "start"),
            _SPAN,
        ),
        ("fixtures/stat_card/payload.json", ("component", "change", "basis", 0), _SPAN),
        ("fixtures/stat_card/payload.json", ("component", "period"), "2018/2022-12"),
        (
            "fixtures/table/payload.json",
            ("component", "rows", 0, "period"),
            "2018-2022",
        ),
    ],
)
def test_a_span_is_rejected_where_a_period_is_a_point(
    relative_path: str, path: tuple[str | int, ...], period: str
) -> None:
    """Line charts, windows and change bases plot or compare single
    periods; a span there, or a span whose ends differ in grain, fails
    validation."""
    payload = _load(relative_path)
    _set_path(payload, path, period)
    with pytest.raises(ValidationError):
        _VALIDATOR.validate(payload)
    assert _component_error_path(payload) == path


def test_a_period_mode_bar_rejects_a_span_point() -> None:
    payload = _bar_in_period_mode()
    _VALIDATOR.validate(payload)  # the control: period mode with point periods
    payload["component"]["series"][0]["points"][0]["period"] = _SPAN
    with pytest.raises(ValidationError):
        _VALIDATOR.validate(payload)
    assert _component_error_path(payload) == (
        "component",
        "series",
        0,
        "points",
        0,
        "period",
    )


_POPULATION = {"stat_var": "dcid:Count_Person", "label": "Total population"}
_CONVERTED = {"currency": "USD", "prices": "constant", "base_year": 2023}

_UNIT_SLOTS = [
    ("fixtures/stat_card/payload.json", ("component", "unit")),
    ("fixtures/line_chart/payload.json", ("component", "series", 0, "unit")),
    ("fixtures/bar_chart/payload.json", ("component", "series", 0, "unit")),
    ("fixtures/table/payload.json", ("component", "rows", 0, "unit")),
]


@pytest.mark.parametrize(("relative_path", "path"), _UNIT_SLOTS)
@pytest.mark.parametrize(
    "unit",
    [
        {"numerator": "US Dollar", "denominator": _POPULATION},
        {"numerator": _CONVERTED, "denominator": _POPULATION},
        {"numerator": _POPULATION, "denominator": "SquareKilometer"},
        {"numerator": "CurrencyUSD", "denominator": _CONVERTED},
    ],
)
def test_a_composed_unit_validates_in_every_unit_slot(
    relative_path: str, path: tuple[str | int, ...], unit: dict
) -> None:
    payload = _load(relative_path)
    _set_path(payload, path, unit)
    _VALIDATOR.validate(payload)  # must not raise


def _errors_with_context(errors: object) -> list[ValidationError]:
    """Every error in the tree, including the ones a `oneOf` holds in its
    `context` for each arm that did not match."""
    found: list[ValidationError] = []
    for error in errors:
        found.append(error)
        found.extend(_errors_with_context(error.context or ()))
    return found


@pytest.mark.parametrize(("relative_path", "path"), _UNIT_SLOTS)
@pytest.mark.parametrize(
    "case",
    [
        (
            {"numerator": "US Dollar", "denominator": _POPULATION, "scale": 1000},
            ("composed_unit",),
            "additionalProperties",
            (),
        ),
        ({"numerator": "US Dollar"}, ("composed_unit",), "required", ()),
        (
            {"numerator": "US Dollar", "denominator": None},
            ("unit_part",),
            "oneOf",
            ("denominator",),
        ),
        (
            {
                "numerator": {"numerator": "US Dollar", "denominator": _POPULATION},
                "denominator": _POPULATION,
            },
            ("unit_part",),
            "oneOf",
            ("numerator",),
        ),
        (
            {
                "numerator": "US Dollar",
                "denominator": {"stat_var": "dcid:Count_Person"},
            },
            ("counted_stat_var",),
            "required",
            ("denominator",),
        ),
        (
            {
                "numerator": "US Dollar",
                "denominator": {"stat_var": "Count_Person", "label": "Population"},
            },
            ("counted_stat_var", "properties", "stat_var"),
            "pattern",
            ("denominator", "stat_var"),
        ),
        (
            {
                "numerator": "US Dollar",
                "denominator": {**_POPULATION, "scale": 1000},
            },
            ("counted_stat_var",),
            "additionalProperties",
            ("denominator",),
        ),
    ],
    ids=[
        "unknown_member",
        "missing_denominator",
        "null_part",
        "nested_composed_part",
        "counted_stat_var_without_label",
        "stat_var_without_dcid_prefix",
        "counted_stat_var_unknown_member",
    ],
)
def test_a_malformed_composed_unit_is_rejected_by_the_rule_it_breaks(
    relative_path: str,
    path: tuple[str | int, ...],
    case: tuple[dict, tuple[str, ...], str, tuple[str, ...]],
) -> None:
    """Each case breaks one rule. The schema reports it from the named
    definition under `$defs`, under the named keyword, at the unit or the
    part that broke it."""
    unit, definition, keyword, part = case
    payload = _load(relative_path)
    _set_path(payload, path, unit)
    with pytest.raises(ValidationError):
        _VALIDATOR.validate(payload)

    component = payload["component"]
    schema = _load(f"schemas/component/{component['type']}.schema.json")
    errors = _errors_with_context(_member_validator(schema).iter_errors(component))
    broken_rule = schema["$defs"]
    for key in definition:
        broken_rule = broken_rule[key]
    assert any(
        error.validator == keyword
        and error.schema == broken_rule
        and ("component", *error.absolute_path) == (*path, *part)
        for error in errors
    )


_CONSTRAINT_FIXTURES = sorted(
    entry.relative_to(_BUNDLE).as_posix()
    for entry in (_BUNDLE / "fixtures" / "constraints").rglob("*.json")
)


@pytest.mark.parametrize("relative_path", _CONSTRAINT_FIXTURES + _FIXTURE_PAYLOAD_PATHS)
def test_a_fixture_component_validates_against_its_member_schema(
    relative_path: str,
) -> None:
    """A constraint case is schema-valid, positive or negative, so a
    negative is refused by the constraint it names and by nothing earlier.
    Every member schema resolves with the member schemas alone, without the
    envelope or the descriptions schemas."""
    component = _load(relative_path)["component"]
    schema = _load(f"schemas/component/{component['type']}.schema.json")
    _member_validator(schema).validate(component)


def _series_units(relative_path: str) -> list[object]:
    return [s["unit"] for s in _load(relative_path)["component"]["series"]]


def test_comparable_units_composed_pair_differs_only_in_the_negative() -> None:
    directory = "fixtures/constraints/comparable_units_required"
    first, second = _series_units(f"{directory}/positive_composed.json")
    assert first == second
    assert set(first) == {"numerator", "denominator"}
    first, second = _series_units(
        f"{directory}/negative_composed_denominator_differs.json"
    )
    assert first != second
    assert first["numerator"] == second["numerator"]


def test_note_narration_fixture_carries_no_frame_provenance() -> None:
    """`note_narration` resolves no frame -- its provenance names no
    inputs and no sources."""
    payload = _load("fixtures/note_narration/payload.json")
    assert payload["provenance"]["inputs"] == []
    assert payload["provenance"]["sources"] == []


def _source_refs(component: dict) -> set[str]:
    if component["type"] == "source_card":
        return set(component["source_refs"])
    rows = component.get("series") or component.get("rows") or []
    return {row["source_ref"] for row in rows}


def _source_ids(payload: dict) -> set[str]:
    if "description" in payload:
        return {source["ref"] for source in payload["description"]["sources"]}
    return {source["id"] for source in payload["provenance"].get("sources", [])}


@pytest.mark.parametrize("relative_path", _FIXTURE_PAYLOAD_PATHS)
def test_fixture_source_refs_name_the_envelopes_own_sources(
    relative_path: str,
) -> None:
    """A member's `source_ref` and `source_refs[]` name a source the same
    envelope holds: a `description.sources[].ref` on a payload with a
    description, a `provenance.sources[].id` otherwise. The schema cannot
    judge this, so the writer keeps it; every fixture does."""
    payload = _load(relative_path)
    assert _source_refs(payload["component"]) <= _source_ids(payload)


def test_source_card_fixture_names_at_least_one_source() -> None:
    assert _load("fixtures/source_card/payload.json")["component"]["source_refs"]


@pytest.mark.parametrize(
    ("relative_path", "path"),
    [
        ("fixtures/line_chart/payload.json", ("component", "series", 0, "source_ref")),
        ("fixtures/bar_chart/payload.json", ("component", "series", 0, "source_ref")),
        ("fixtures/table/payload.json", ("component", "rows", 0, "source_ref")),
        ("fixtures/source_card/payload.json", ("component", "source_refs", 0)),
    ],
)
def test_a_payload_without_a_description_holds_source_refs_to_source_ids(
    relative_path: str, path: tuple[str | int, ...]
) -> None:
    """A payload without a description names its sources by
    `provenance.sources[].id`, so a provenance dcid, which a member schema
    admits for a described payload, is refused there."""
    payload = _load(relative_path)
    _set_path(payload, path, "provenance/OECDCreditorReportingSystem")
    assert any(
        error.validator == "pattern" and tuple(error.absolute_path) == path
        for error in _errors_with_context(_VALIDATOR.iter_errors(payload))
    )


@pytest.mark.parametrize(
    ("relative_path", "path"),
    [
        ("fixtures/line_chart/payload.json", ("component_id",)),
        ("fixtures/line_chart/payload.json", ("catalog_version",)),
        ("fixtures/line_chart/payload.json", ("provenance", "inputs", 0, "spec_id")),
        (
            "fixtures/line_chart/payload.json",
            ("provenance", "inputs", 0, "content_version"),
        ),
        ("fixtures/line_chart/payload.json", ("provenance", "sources", 0, "id")),
        ("fixtures/line_chart/payload.json", ("component", "series", 0, "id")),
        ("fixtures/line_chart/payload.json", ("component", "series", 0, "source_ref")),
        ("fixtures/bar_chart/payload.json", ("component", "series", 0, "id")),
        ("fixtures/table/payload.json", ("component", "rows", 0, "series_id")),
        ("fixtures/table/payload.json", ("component", "rows", 0, "source_ref")),
        ("fixtures/source_card/payload.json", ("component", "source_refs", 0)),
    ],
)
def test_an_id_with_a_trailing_newline_is_rejected(
    relative_path: str, path: tuple[str | int, ...]
) -> None:
    """Python's `re.search` lets a `$` match before a final newline, so each
    anchored id pattern also refuses a newline."""
    payload = _load(relative_path)
    value = payload
    for key in path:
        value = value[key]
    _set_path(payload, path, f"{value}\n")
    assert any(
        tuple(error.absolute_path) == path
        for error in _errors_with_context(_VALIDATOR.iter_errors(payload))
    )


def test_a_frame_bearing_payload_with_empty_inputs_is_rejected() -> None:
    """`provenance.inputs` stays required non-empty for every member but
    `note_narration` -- the envelope schema's `if`/`else` on `component.type`
    only relaxes that bound for the one member with no frame to name."""
    payload = _load("fixtures/line_chart/payload.json")
    payload["provenance"]["inputs"] = []
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert list(excinfo.value.absolute_path) == ["provenance", "inputs"]


def test_note_narration_with_empty_inputs_still_validates() -> None:
    payload = _load("fixtures/note_narration/payload.json")
    assert payload["provenance"]["inputs"] == []
    _VALIDATOR.validate(payload)  # must not raise


def test_an_analysis_minted_frame_ref_validates() -> None:
    """A frame an Analysis Toolkit verb minted carries an `ops_` spec_id and
    an `ocv_` content_version. Both halves of that pair validate, the way
    both halves of a Datasets `spec_`/`cv_` pair do."""
    payload = _load("fixtures/line_chart/payload.json")
    payload["provenance"]["inputs"] = [
        {
            "spec_id": "ops_9f2e41a7c03b58d2a4c1f6b2e8d35790",
            "content_version": "ocv_f4a81c22d9075e3ba61c2ee98f83b405",
        }
    ]
    _VALIDATOR.validate(payload)  # must not raise


def test_an_unknown_content_version_prefix_is_rejected() -> None:
    """`cv_` and `ocv_` are the whole vocabulary. A third prefix is a
    malformed token, not a namespace this schema has yet to hear about."""
    payload = _load("fixtures/line_chart/payload.json")
    payload["provenance"]["inputs"] = [
        {
            "spec_id": "spec_9f2e41a7c03b58d2a4c1f6b2e8d35790",
            "content_version": "xcv_f4a81c22d9075e3ba61c2ee98f83b405",
        }
    ]
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert "content_version" in list(excinfo.value.absolute_path)


def _payload_with_source_members(**members: object) -> dict:
    payload = _load("fixtures/line_chart/payload.json")
    payload["provenance"]["sources"][0].update(members)
    return payload


def test_a_source_item_may_omit_source_and_data_commons() -> None:
    """Dataset versions minted before the origin catalogue keep serving as
    minted, so the fixture's items carry neither member and still validate."""
    payload = _load("fixtures/line_chart/payload.json")
    assert "source" not in payload["provenance"]["sources"][0]
    _VALIDATOR.validate(payload)  # must not raise


def test_a_source_item_validates_with_and_without_ref() -> None:
    """`ref` is the provenance dcid; components minted before platforms wrote
    it carry none and keep validating."""
    with_ref = _load("fixtures/bar_chart/payload.json")
    without_ref = _load("fixtures/line_chart/payload.json")
    assert with_ref["provenance"]["sources"][0]["ref"]
    assert "ref" not in without_ref["provenance"]["sources"][0]
    _VALIDATOR.validate(with_ref)  # must not raise
    _VALIDATOR.validate(without_ref)  # must not raise


def test_an_empty_source_ref_is_rejected() -> None:
    payload = _payload_with_source_members(ref="")
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert list(excinfo.value.absolute_path)[-1] == "ref"


@pytest.mark.parametrize("data_commons_id", ["dfo", "base", "un_system"])
def test_a_source_item_with_source_and_data_commons_validates(
    data_commons_id: str,
) -> None:
    payload = _payload_with_source_members(
        source={"id": "dc/s/WorldBank", "name": "World Bank"},
        data_commons={"id": data_commons_id, "name": "Data Commons"},
    )
    _VALIDATOR.validate(payload)  # must not raise


def test_an_unknown_data_commons_id_is_rejected() -> None:
    payload = _payload_with_source_members(
        data_commons={"id": "other", "name": "Other"}
    )
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert list(excinfo.value.absolute_path)[-2:] == ["data_commons", "id"]


@pytest.mark.parametrize("member", ["source", "data_commons"])
def test_a_source_item_member_missing_its_name_is_rejected(member: str) -> None:
    ref = {"id": "dfo" if member == "data_commons" else "dc/s/WorldBank"}
    payload = _payload_with_source_members(**{member: ref})
    with pytest.raises(ValidationError):
        _VALIDATOR.validate(payload)


_DESCRIBED = "fixtures/line_chart/payload_description.json"
_MOVED_MEMBERS = ["sources", "freshness", "warnings", "citation"]


def _description_vector(name: str) -> dict:
    return json.loads(
        (schema_dir().parent / "vectors" / name).read_text(encoding="utf-8")
    )


def test_a_described_fixture_carries_the_card_revision_vector() -> None:
    """The fixture's `description` is the input of the descriptions suite's
    card-revision vector, so the two cannot drift apart."""
    vector = _description_vector("description-card-revision.json")
    assert _load(_DESCRIBED)["description"] == vector["input"]


def test_a_described_fixture_describes_its_own_revision() -> None:
    """The schema holds a card's description to the `card_revision` kind; that
    it names this card and this revision is the writer's to keep."""
    payload = _load(_DESCRIBED)
    assert payload["description"]["describes"] == {
        "kind": "card_revision",
        "component_id": payload["component_id"],
        "revision": payload["revision"],
    }


@pytest.mark.parametrize(
    "relative_path", [_DESCRIBED, "fixtures/note_narration/payload_lineage.json"]
)
def test_a_lineage_only_fixture_carries_none_of_the_moved_members(
    relative_path: str,
) -> None:
    assert not set(_MOVED_MEMBERS) & set(_load(relative_path)["provenance"])


@pytest.mark.parametrize("member", _MOVED_MEMBERS)
def test_a_data_card_without_a_description_rejects_a_missing_moved_member(
    member: str,
) -> None:
    """A data card issued without a description keeps its sources, freshness,
    warnings and citation in `provenance`, so dropping one leaves its panel
    with nothing to show."""
    payload = _load("fixtures/line_chart/payload.json")
    del payload["provenance"][member]
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert excinfo.value.validator == "required"
    assert list(excinfo.value.absolute_path) == ["provenance"]


def test_a_note_without_a_description_carries_lineage_alone() -> None:
    """A narration note draws no data, so it needs neither a description nor
    the moved members."""
    payload = _load("fixtures/note_narration/payload_lineage.json")
    assert "description" not in payload
    _VALIDATOR.validate(payload)  # must not raise


@pytest.mark.parametrize("member", _MOVED_MEMBERS)
def test_a_described_payload_rejects_a_moved_member_in_provenance(
    member: str,
) -> None:
    """A card with a description keeps lineage alone in `provenance`, so a
    source, freshness, warning or citation there is a second account of what
    the description says."""
    legacy = _load("fixtures/line_chart/payload.json")["provenance"]
    payload = _load(_DESCRIBED)
    payload["provenance"][member] = legacy[member]
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    # jsonschema reports a `false` subschema at its parent object, so the
    # refused member shows as the error's instance.
    assert excinfo.value.schema is False
    assert excinfo.value.instance == legacy[member]


@pytest.mark.parametrize(
    "member", ["inputs", "execution_ref", "generated_at", "refreshed_from"]
)
def test_a_payload_missing_a_lineage_member_is_rejected(member: str) -> None:
    payload = _load(_DESCRIBED)
    del payload["provenance"][member]
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert excinfo.value.validator == "required"
    assert list(excinfo.value.absolute_path) == ["provenance"]


def _description_errors(payload: dict) -> list[ValidationError]:
    return [
        error
        for error in _errors_with_context(_VALIDATOR.iter_errors(payload))
        if list(error.absolute_path)[:1] == ["description"]
    ]


@pytest.mark.parametrize(
    ("mutate", "keyword", "path"),
    [
        (lambda d: d.pop("cite"), "required", []),
        (
            lambda d: d.update(summary="drawn from three sectors"),
            "additionalProperties",
            [],
        ),
        (
            lambda d: d["caveats"][0].update(kind="imputed"),
            "enum",
            ["caveats", 0, "kind"],
        ),
        (
            lambda d: d["steps"][0]["input"].pop("content_version"),
            "required",
            ["steps", 0, "input"],
        ),
    ],
    ids=["missing_cite", "unknown_member", "caveat_kind", "step_input_version"],
)
def test_a_description_failing_its_schema_is_rejected(
    mutate: Callable[[dict], object], keyword: str, path: list[str | int]
) -> None:
    """The envelope reaches the description schema, and the step schema
    under it, by `$id`; each case breaks one rule of theirs."""
    payload = _load(_DESCRIBED)
    mutate(payload["description"])
    with pytest.raises(ValidationError):
        _VALIDATOR.validate(payload)
    assert any(
        error.validator == keyword
        and list(error.absolute_path) == ["description", *path]
        for error in _description_errors(payload)
    )


def test_a_version_description_on_a_card_is_rejected() -> None:
    """A card carries its own revision's description. The dataset version's
    description in the `description-worked-example` vector validates against the
    description schema and is still refused on a card."""
    vector = _description_vector("description-worked-example.json")
    Draft202012Validator(description_schema(), registry=_REGISTRY).validate(
        vector["input"]
    )
    payload = _load(_DESCRIBED)
    payload["description"] = vector["input"]
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert list(excinfo.value.absolute_path) == ["description", "describes", "kind"]


def test_an_unknown_envelope_member_is_rejected() -> None:
    payload = _load(_DESCRIBED)
    payload["descriptions"] = payload["description"]
    with pytest.raises(ValidationError) as excinfo:
        _VALIDATOR.validate(payload)
    assert excinfo.value.validator in {"additionalProperties", "unevaluatedProperties"}
