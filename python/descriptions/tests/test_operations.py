"""The operation list against its schema and the step schema it describes."""

from __future__ import annotations

import jsonschema
from referencing import Registry, Resource

from dfo_descriptions import operations, operations_schema, schema_dir, steps_schema


def test_operations_validate_against_their_schema() -> None:
    schema = operations_schema()
    registry = Registry().with_resources(
        [(steps_schema()["$id"], Resource.from_contents(steps_schema()))]
    )
    jsonschema.Draft202012Validator(schema, registry=registry).validate(operations())


def test_what_a_count_counts_is_one_vocabulary() -> None:
    counted = operations_schema()["$defs"]["operation"]["properties"]["left_out"][
        "items"
    ]["properties"]["of"]["items"]["enum"]
    assert counted == steps_schema()["$defs"]["left_out"]["properties"]["of"]["enum"]


def test_every_policy_is_named_once() -> None:
    policies = [entry["policy"] for entry in operations()["operations"]]
    assert len(policies) == len(set(policies))


def test_schema_dir_holds_the_packaged_files() -> None:
    names = {path.name for path in schema_dir().iterdir()}
    assert names >= {
        "steps.schema.json",
        "description.schema.json",
        "operations.json",
        "operations.schema.json",
    }
