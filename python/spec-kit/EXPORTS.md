# dfo-spec-kit export manifest

This file defines the compatibility boundary of the `dfo-spec-kit` Python
distribution (import `dfo_spec_kit`): every name a consumer outside this
package may import, and nothing else. A module not named in `modules` below
is private by construction. A name in a listed module but absent from the
manifest is likewise not public, whatever that module's `__all__` says
today — this file, not a module's own `__all__`, is the compatibility
contract a consumer is entitled to rely on.

What each symbol _means_ lives in its own docstring and in the JSON Schema
under `schemas/spec/`. This file records only what is published and under
what compatibility promise, so that there is one place to check and one
place to change.

## Admission rule

A symbol is listed only if it is contract-defined — its shape is fixed by a
specification or wire contract this distribution implements, not an
incidental implementation detail — and either has at least two independent
consumers, or is required to generate a published JSON Schema.

The rule keeps the compatibility promise no larger than the surface
something outside this package actually depends on. A symbol that fails it
can still exist in the source; it just is not a promise anyone gets to lean
on.

### Grandfathering

Three cases are admitted without satisfying the rule directly. Each entry
below names which one applies in its `reason`.

1. **`curated`** — the symbol was already in an existing, curated `__all__`
   when this manifest was written. That curation is not redone here.
1. **`bundled-data`** — the symbol is typed access to a data file this
   distribution bundles, where the file is itself contract-defined. The
   file's contract is the evidence; the access layer is required to read it
   at all.
1. **`sub-shape`** — the symbol is reachable only from inside a root symbol
   that satisfies the rule independently. It is never imported except as
   part of that root's shape, so it cannot accumulate consumers of its own.

Grandfathering changes admission, not stability: a grandfathered symbol
still carries its own `stability` value, governed the same way as any
other.

## Stability

`stable` means changing the symbol — removing it, narrowing a type,
changing a signature or return shape — is a breaking change under
`policies/dataset-spec.toml`. `provisional` means the symbol is real and
usable but its shape, or its split from neighbouring symbols, is still
expected to move. A provisional change is still a version bump, just not
necessarily a major one.

## Compatibility by kind

Unless an entry carries its own `policy`, these rules apply by `kind`:

| `kind`      | Additive                              | Breaking                                                       |
| ----------- | ------------------------------------- | -------------------------------------------------------------- |
| `model`     | adding an optional field              | removing a field, narrowing a type, accepting new members      |
| `alias`     | —                                     | adding, removing, or altering a union arm or scalar constraint |
| `function`  | a new optional keyword-only parameter | any other signature or return-shape change                     |
| `constant`  | —                                     | changing the value                                             |
| `exception` | adding a category spelling            | removing or redefining an existing category spelling           |

## Manifest

The block below is normative and parses with the standard library's
`tomllib`. Prose elsewhere in this file is commentary.

`reason` records why the symbol is admitted: either the evidence class that
satisfies the admission rule, or which grandfathering case applies. It
deliberately names no file paths — a citation into another repository
cannot be checked from here and goes stale on that repository's next
refactor. The evidence for an addition belongs in the change that makes it.

```toml
format_version = 1
distribution = "dfo-spec-kit"
version = "0.2.0"

# Closed. A module not listed is private; dfo_spec_kit.identity.core is the
# running example -- its public names are manifested under the module that
# re-exports them, never under core directly.
modules = [
  "dfo_spec_kit",
  "dfo_spec_kit.identity",
  "dfo_spec_kit.models",
  "dfo_spec_kit.registry",
]

# --- dfo_spec_kit ------------------------------------------------------
[[export]]
name = "__version__"
module = "dfo_spec_kit"
kind = "constant"
stability = "stable"
policy = "tracks the distribution's own release version"
reason = "curated"

# --- dfo_spec_kit.identity ---------------------------------------------
# The spec1 identity scheme: parse, validate, normalise, canonicalise, mint.
[[export]]
name = "IDENTITY_VERSION"
module = "dfo_spec_kit.identity"
kind = "constant"
stability = "stable"
policy = "the value is permanent; a new identity generation mints an additional constant"
reason = "curated"

[[export]]
name = "IdentityError"
module = "dfo_spec_kit.identity"
kind = "exception"
stability = "stable"
reason = "raised across the identity pipeline and caught by five independent modules"

[[export]]
name = "parse_identity_bytes"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "stable"
reason = "curated; the strict-parse entry point, exercised only by conformance suites so far"

[[export]]
name = "validate_identity_core"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "stable"
reason = "two independent consumers"

[[export]]
name = "build_projection"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "provisional"
reason = "curated; one consumer, and its split from canonicalize may still move"

[[export]]
name = "canonicalize"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "stable"
reason = "a general JCS primitive reused by another document family, independent of spec_id minting"

[[export]]
name = "mint_spec_id"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "provisional"
reason = "curated; one consumer, and its split from build_projection may still move"

[[export]]
name = "spec_id_from_bytes"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "stable"
reason = "curated; the bytes-in one-call path, with no consumer yet"

[[export]]
name = "spec_id_from_value"
module = "dfo_spec_kit.identity"
kind = "function"
stability = "stable"
reason = "the value-in one-call path; three independent consumers"

# --- dfo_spec_kit.registry ---------------------------------------------
# Typed access to the bundled code registry mirror; see
# schemas/registry/README.md for the data file's own contract.
[[export]]
name = "REGISTRY"
module = "dfo_spec_kit.registry"
kind = "constant"
stability = "provisional"
policy = "contents track registries/codes.json under that file's addition rule"
reason = "bundled-data"

[[export]]
name = "Classification"
module = "dfo_spec_kit.registry"
kind = "alias"
stability = "provisional"
reason = "bundled-data"

[[export]]
name = "Lifecycle"
module = "dfo_spec_kit.registry"
kind = "alias"
stability = "provisional"
reason = "bundled-data"

[[export]]
name = "CodeMapping"
module = "dfo_spec_kit.registry"
kind = "model"
stability = "provisional"
reason = "bundled-data"

[[export]]
name = "CodeEntry"
module = "dfo_spec_kit.registry"
kind = "model"
stability = "provisional"
reason = "bundled-data"

[[export]]
name = "CodeRegistry"
module = "dfo_spec_kit.registry"
kind = "model"
stability = "provisional"
reason = "bundled-data"

[[export]]
name = "load_registry"
module = "dfo_spec_kit.registry"
kind = "function"
stability = "provisional"
reason = "bundled-data"

# --- dfo_spec_kit.models -----------------------------------------------
# The spec1 identity-core document and the canonical definition wrapping
# it. Extracted from dfo-apis's Search engine; the extraction commit is the
# provenance. Nothing display-shaped belongs here: a symbol carrying a
# graph-read label or a human-facing name is engine surface, not identity.
[[export]]
name = "BaseIdentity"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "the hashed identity document; four independent consumers, and the root of a published schema"

[[export]]
name = "IdentitySlot"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape"

[[export]]
name = "IdentityBindingValue"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape; also constructed directly by four independent consumers"

[[export]]
name = "IdentityBindingSet"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape; also constructed directly by four independent consumers"

[[export]]
name = "IdentityBindingUnbound"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape"

[[export]]
name = "IdentityBinding"
module = "dfo_spec_kit.models"
kind = "alias"
stability = "stable"
reason = "sub-shape; the closed four-state slot binding union"

[[export]]
name = "IdentityWindowAll"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape"

[[export]]
name = "IdentityWindowLatest"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape"

[[export]]
name = "IdentityWindowRange"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape"

[[export]]
name = "IdentityWindow"
module = "dfo_spec_kit.models"
kind = "alias"
stability = "stable"
reason = "sub-shape; the closed three-state requested-window union"

[[export]]
name = "IdentityFacetValueRequest"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape; also constructed directly by four independent consumers"

[[export]]
name = "IdentityUnitRequest"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "sub-shape; also constructed directly by three independent consumers"

[[export]]
name = "IdentityFacetBinding"
module = "dfo_spec_kit.models"
kind = "alias"
stability = "stable"
reason = "sub-shape; the facet binding union, referenced by eight independent consumers"

[[export]]
name = "IdentityString"
module = "dfo_spec_kit.models"
kind = "alias"
stability = "stable"
policy = "widening the length cap is additive; narrowing it, or adding a minimum, is breaking"
reason = "sub-shape; every string leaf of a posted identity document"

[[export]]
name = "SpecDefinition"
module = "dfo_spec_kit.models"
kind = "model"
stability = "stable"
reason = "the canonical definition a registry stores and a permalink carries; the root of a published schema"
```

## Excluded models

Two families in the Search engine's models are deliberately outside this
boundary, and their absence is a decision rather than an oversight.

- **The resubmit edit input.** Its slots are the engine's display shape,
  which carries graph-read labels and human-facing names. It also has a
  reshape already planned — see rows C13, C14 and C18 of
  `dfo-apis:docs/conformance-search-api-v1.md`.
- **The resolved-spec response payload.** One surface produces it, no
  surface outside that one consumes it, and it reuses the same
  display-shaped types as the edit input.

A planned reshape is reason enough to withhold a symbol, independent of how
many consumers it has.

## Known gaps

Two things a consumer reimplements because this distribution does not
export them. Both are candidate additions, not omissions to correct
silently.

- **Verifying a `spec_id`.** Recomputing an id and comparing it against a
  supplied one is done caller-side today, in three places.
- **A byte cap on the parsed-value path.** Document size is measured only
  on the raw-bytes entry point, so a caller entering with an already-parsed
  value maintains its own cap.

## Versioning

`policies/dataset-spec.toml` states what change to these exports requires
which version bump, and what is immutable once a release is tagged.
