"""Cross-checks the manifest's `retry_modes` map against the canonical
code registry (`registries/codes.json` at the repo root), read from this
checkout the same repo-root-relative way, keeping the test hermetic.

Every retry-mode key this bundle carries, its own seven plus the platform-
generic codes (`not_found`, `service_unavailable`, `internal_error`,
`request_invalid`, `timeout`) its own doors and the platform's default
classification also render for it, is a registered, refusal-classified
code mapped onto `presentation.write`.
"""

from __future__ import annotations

import json
from pathlib import Path

from dfo_spec_kit_components import bundle_dir

# tests/ -> dfo-spec-kit-components -> python -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO_ROOT / "registries" / "codes.json"


def _manifest_retry_modes() -> dict[str, str]:
    manifest = json.loads((bundle_dir() / "manifest.json").read_text(encoding="utf-8"))
    return manifest["retry_modes"]


def test_registry_is_reachable_from_this_worktree() -> None:
    assert _REGISTRY_PATH.exists(), (
        f"expected the canonical registry at {_REGISTRY_PATH}"
    )


def test_presentation_specific_retry_mode_codes_are_registered_refusals() -> None:
    registry = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    codes_by_name = {entry["code"]: entry for entry in registry["entries"]}

    retry_mode_codes = set(_manifest_retry_modes())
    assert retry_mode_codes, "expected at least one retry-mode code"

    missing = retry_mode_codes - set(codes_by_name)
    assert not missing, (
        f"retry_modes names codes absent from the registry: {sorted(missing)}"
    )

    not_refusals = {
        code
        for code in retry_mode_codes
        if codes_by_name[code]["classification"] != "refusal"
    }
    assert not not_refusals, (
        f"retry_modes names codes not classified as refusals in the registry: "
        f"{sorted(not_refusals)}"
    )


def test_presentation_codes_map_to_the_presentation_write_endpoint() -> None:
    registry = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    codes_by_name = {entry["code"]: entry for entry in registry["entries"]}
    retry_mode_codes = set(_manifest_retry_modes())

    for code in retry_mode_codes:
        endpoints = {mapping["endpoint"] for mapping in codes_by_name[code]["mappings"]}
        assert "presentation.write" in endpoints, (
            f"{code}: expected a presentation.write mapping, found {sorted(endpoints)}"
        )
