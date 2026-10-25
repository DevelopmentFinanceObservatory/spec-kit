"""The packaged contract files, read from package data."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

# The rules under `rules/`, each a file of vectors named after the rule.
RULES = (
    "key-format",
    "credentials-path",
    "credentials-file",
    "key-lookup",
    "cache-dir",
    "cache-layout",
)


def contract_dir() -> Path:
    """The installed contract directory.

    Resolves through `importlib.resources`, so it works from a repo checkout
    and from an installed wheel. A scenario's binary body file sits in its
    `scenarios/` directory.
    """
    return Path(str(resources.files("dfo_client_contract")))


def _load(relative: str) -> Any:
    return json.loads((contract_dir() / relative).read_text(encoding="utf-8"))


def _records(directory: str) -> list[dict[str, Any]]:
    paths = sorted((contract_dir() / directory).glob("*.json"))
    if not paths:
        raise RuntimeError(f"no records found under dfo_client_contract/{directory}")
    return [json.loads(path.read_text(encoding="utf-8")) for path in paths]


def openapi() -> dict[str, Any]:
    """`served/openapi.json`: the composed dfo-apis app's OpenAPI document."""
    return _load("served/openapi.json")


def served_source() -> dict[str, Any]:
    """`served/SOURCE.json`: the dfo-apis commit `openapi()` was captured from."""
    return _load("served/SOURCE.json")


def strict_fields() -> dict[str, Any]:
    """`strict-fields.json`: the response members the clients act on."""
    return _load("strict-fields.json")


def strict_fields_schema() -> dict[str, Any]:
    """`strict-fields.schema.json`: the shape of `strict-fields.json`."""
    return _load("strict-fields.schema.json")


def actions() -> dict[str, Any]:
    """`actions.json`: the caller's next step for each refusal code."""
    return _load("actions.json")


def actions_schema() -> dict[str, Any]:
    """`actions.schema.json`: the shape of `actions.json`."""
    return _load("actions.schema.json")


def client_codes() -> dict[str, Any]:
    """`client-codes.json`: the code of each error a client raises itself."""
    return _load("client-codes.json")


def client_codes_schema() -> dict[str, Any]:
    """`client-codes.schema.json`: the shape of `client-codes.json`."""
    return _load("client-codes.schema.json")


def proxy_codes() -> dict[str, Any]:
    """`proxy-codes.json`: the codes of the website proxy's own refusals."""
    return _load("proxy-codes.json")


def proxy_codes_schema() -> dict[str, Any]:
    """`proxy-codes.schema.json`: the shape of `proxy-codes.json`."""
    return _load("proxy-codes.schema.json")


def warning_codes() -> dict[str, Any]:
    """`warning-codes.json`: the code of each warning a client issues itself."""
    return _load("warning-codes.json")


def warning_codes_schema() -> dict[str, Any]:
    """`warning-codes.schema.json`: the shape of `warning-codes.json`."""
    return _load("warning-codes.schema.json")


def scenario_schema() -> dict[str, Any]:
    """`scenario.schema.json`: the shape of one scenario record."""
    return _load("scenario.schema.json")


def dataset_ref_schema() -> dict[str, Any]:
    """`dataset-ref.schema.json`: the frozen DatasetRef format."""
    return _load("dataset-ref.schema.json")


def rules_schema() -> dict[str, Any]:
    """`rules/rules.schema.json`: the shape of every rule file."""
    return _load("rules/rules.schema.json")


def scenarios() -> list[dict[str, Any]]:
    """Every scenario record, sorted by file name.

    Raises:
        RuntimeError: No scenario record is installed.
    """
    return _records("scenarios")


def ref_examples() -> list[dict[str, Any]]:
    """Every DatasetRef example, sorted by file name.

    Raises:
        RuntimeError: No example is installed.
    """
    return _records("refs")


def rule(name: str) -> dict[str, Any]:
    """The vector file for rule `name`, one of `RULES`.

    Raises:
        ValueError: `name` is not in `RULES`.
    """
    if name not in RULES:
        raise ValueError(f"unknown rule {name!r}; expected one of {RULES}")
    return _load(f"rules/{name}.json")
