"""Step records and descriptions.

Ships the step schema, the description schema, the operation list and
their vector suite as package data, and checks the structure rules a step
list and a description must meet beyond their schemas. `check_steps` and
`check_description` are pure. They raise `StepsError`, whose `reason` is
drawn from `StepsErrorReason`, on the first rule broken.
"""

from dfo_descriptions.schemas import (
    description_schema,
    operations,
    operations_schema,
    schema_dir,
    steps_schema,
)
from dfo_descriptions.structure import (
    MAX_CITE_TEXT_LENGTH,
    MAX_STEPS,
    MAX_STEPS_BYTES,
    StepsError,
    StepsErrorReason,
    check_description,
    check_steps,
    steps_bytes,
)

__all__ = [
    "MAX_CITE_TEXT_LENGTH",
    "MAX_STEPS",
    "MAX_STEPS_BYTES",
    "StepsError",
    "StepsErrorReason",
    "check_description",
    "check_steps",
    "description_schema",
    "operations",
    "operations_schema",
    "schema_dir",
    "steps_bytes",
    "steps_schema",
]
