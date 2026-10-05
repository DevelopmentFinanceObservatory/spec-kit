"""Typed access to the platform code registry.

The registry (`registries/codes.json` at the repo root; see
`schemas/registry/README.md` and `registries/README.md` for the format it
validates against) is the platform's single, language-neutral catalogue of
permanent warning and refusal codes. `registries/codes.json` stays canonical;
`codes.json` in this package is a byte-identical mirror kept in sync by
`tools/validate_registry.py`, which fails loudly if the two ever diverge.

This module loads the packaged mirror through `importlib.resources`, so it
works the same from an installed wheel as from a repo checkout, and exposes
it as a small set of frozen dataclasses answering: what codes exist, what is
each one's classification, and which endpoint/door/channel combinations is
each permitted on. `REGISTRY` is the packaged registry, loaded once at import
time; `load_registry` is exposed separately for a caller that wants to parse
a different registry snapshot (e.g. in a test).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal

Classification = Literal["refusal", "warning"]
Lifecycle = Literal["active", "retired"]


@dataclass(frozen=True, slots=True)
class CodeMapping:
    """One endpoint/door/channel combination a code is allowed to appear on.

    `doors` is `None` when the mapping applies regardless of door (the
    registry format's own convention for an omitted `doors` member).
    `http_status` is set only when `channel` is `"problem_details"`.
    """

    endpoint: str
    channel: str
    doors: tuple[str, ...] | None = None
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class CodeEntry:
    """One registered warning or refusal code."""

    code: str
    classification: Classification
    definition: str
    owning_contract: str
    owner: str
    introduced_in: str
    mappings: tuple[CodeMapping, ...]
    lifecycle: Lifecycle

    def permits(self, *, endpoint: str, channel: str, door: str | None = None) -> bool:
        """Whether this code is registered to appear on `endpoint`/`channel`.

        `door`, when given, additionally requires the matching mapping to
        either declare no doors of its own (applies regardless of door) or
        list `door` among the ones it declares.
        """
        return any(
            mapping.endpoint == endpoint
            and mapping.channel == channel
            and (door is None or mapping.doors is None or door in mapping.doors)
            for mapping in self.mappings
        )


class CodeRegistry:
    """Every entry in the platform code registry, keyed by `code`.

    Construct via `load_registry`; `REGISTRY` is the packaged registry
    loaded once at import time.
    """

    def __init__(self, *, format_version: int, entries: tuple[CodeEntry, ...]) -> None:
        self.format_version = format_version
        self.entries = entries
        self._by_code = {entry.code: entry for entry in entries}

    def __getitem__(self, code: str) -> CodeEntry:
        """The entry for `code`. Raises `KeyError` if `code` is not registered."""
        return self._by_code[code]

    def __contains__(self, code: str) -> bool:
        return code in self._by_code

    def __iter__(self) -> Any:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @property
    def codes(self) -> frozenset[str]:
        """Every registered code spelling, active and retired alike."""
        return frozenset(self._by_code)


def _mapping_from_json(raw: dict[str, Any]) -> CodeMapping:
    doors = raw.get("doors")
    return CodeMapping(
        endpoint=raw["endpoint"],
        channel=raw["channel"],
        doors=tuple(doors) if doors is not None else None,
        http_status=raw.get("http_status"),
    )


def _entry_from_json(raw: dict[str, Any]) -> CodeEntry:
    return CodeEntry(
        code=raw["code"],
        classification=raw["classification"],
        definition=raw["definition"],
        owning_contract=raw["owning_contract"],
        owner=raw["owner"],
        introduced_in=raw["introduced_in"],
        mappings=tuple(_mapping_from_json(m) for m in raw["mappings"]),
        lifecycle=raw["lifecycle"],
    )


def load_registry() -> CodeRegistry:
    """Load and parse the packaged registry mirror (`registry/codes.json`)."""
    raw_text = (
        resources.files("dfo_spec_kit.registry")
        .joinpath("codes.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(raw_text)
    entries = tuple(_entry_from_json(entry) for entry in data["entries"])
    return CodeRegistry(format_version=data["format_version"], entries=entries)


REGISTRY: CodeRegistry = load_registry()

__all__ = [
    "REGISTRY",
    "Classification",
    "CodeEntry",
    "CodeMapping",
    "CodeRegistry",
    "Lifecycle",
    "load_registry",
]
