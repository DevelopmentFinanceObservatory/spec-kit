# registries

`codes.json` is the seeded instance of the platform's code registry, every permanent warning and refusal code across the contracts this kit distributes, sorted by code. The format (field semantics, the addition rule, classification, and the endpoint/door/channel vocabulary) is documented in `schemas/registry/README.md`, which also owns the JSON Schema that validates this file. Run `uv run tools/validate_registry.py` after editing it.

This file is the canonical copy. `python/spec-kit/src/dfo_spec_kit/registry/codes.json` is a byte-identical package mirror, kept in sync by the same validator, so a Python consumer can load the registry from an installed wheel via `dfo_spec_kit.registry` without a filesystem path back to this directory. Edit this file, not the mirror; `uv run tools/validate_registry.py` fails loudly if they diverge.
