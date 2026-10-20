"""The DFO client contract.

Ships, as package data, what the DFO Python and TypeScript clients generate
from and replay: the served OpenAPI document, the strict fields, the caller
action for each refusal code, the scenario and DatasetRef formats with their
records, and the client rules with their vectors. Every accessor reads the
installed files through `importlib.resources`.
"""

from dfo_client_contract.contract import (
    RULES,
    actions,
    actions_schema,
    contract_dir,
    dataset_ref_schema,
    openapi,
    ref_examples,
    rule,
    rules_schema,
    scenario_schema,
    scenarios,
    served_source,
    strict_fields,
    strict_fields_schema,
)

__all__ = [
    "RULES",
    "actions",
    "actions_schema",
    "contract_dir",
    "dataset_ref_schema",
    "openapi",
    "ref_examples",
    "rule",
    "rules_schema",
    "scenario_schema",
    "scenarios",
    "served_source",
    "strict_fields",
    "strict_fields_schema",
]
