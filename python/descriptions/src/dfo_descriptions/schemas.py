"""The packaged schemas and operation list, read from package data."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


def schema_dir() -> Path:
    """The installed `schemas/` directory.

    Resolves through `importlib.resources`, so it works from a repo checkout
    and from an installed wheel.
    """
    return Path(str(resources.files("dfo_descriptions").joinpath("schemas")))


def _load(name: str) -> dict[str, Any]:
    return json.loads((schema_dir() / name).read_text(encoding="utf-8"))


def steps_schema() -> dict[str, Any]:
    """`steps.schema.json`: the step list."""
    return _load("steps.schema.json")


def description_schema() -> dict[str, Any]:
    """`description.schema.json`: the description, whose `steps` references
    `steps.schema.json` by relative `$ref`."""
    return _load("description.schema.json")


def operations() -> dict[str, Any]:
    """`operations.json`: the operations a step can name."""
    return _load("operations.json")


def operations_schema() -> dict[str, Any]:
    """`operations.schema.json`: the shape of `operations.json`."""
    return _load("operations.schema.json")
