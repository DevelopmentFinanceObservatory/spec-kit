"""Tests for dfo_spec_kit."""

import dfo_spec_kit
from dfo_spec_kit import identity


def test_version() -> None:
    """Test that version is defined."""
    assert dfo_spec_kit.__version__ == "0.2.0"


def test_identity_version_constant() -> None:
    """IDENTITY_VERSION is importable from the identity subpackage and pins
    the spec1 literal."""
    assert identity.IDENTITY_VERSION == "spec1"


def test_top_level_exports_only_version() -> None:
    """Each name is public from one path only. The top-level package
    re-exports nothing from its subpackages, so a consumer imports
    IDENTITY_VERSION and REGISTRY from dfo_spec_kit.identity and
    dfo_spec_kit.registry respectively."""
    assert dfo_spec_kit.__all__ == ["__version__"]
