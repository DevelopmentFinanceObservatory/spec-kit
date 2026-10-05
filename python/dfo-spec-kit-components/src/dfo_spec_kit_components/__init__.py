"""The presentation contract bundle, shipped as package data.

`catalogue/` carries the envelope and component JSON Schemas, the write-side
intent grammar (`schemas/intent.v0.json`), minimal and worked intents,
resolved-payload fixtures, constraint fixtures, and the manifest that
digests all of it.

The Python surface is one function. `bundle_dir` locates the installed
bundle so a consumer can load its contents directly.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

__all__ = ["bundle_dir"]


def bundle_dir() -> Path:
    """The installed `catalogue/` bundle directory.

    Resolves through `importlib.resources`, so it works from a repo
    checkout and from an installed wheel.
    """
    return Path(str(resources.files("dfo_spec_kit_components").joinpath("catalogue")))
