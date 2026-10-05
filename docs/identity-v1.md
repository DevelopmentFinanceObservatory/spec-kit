# Identity (v1)

This document specifies what a spec1 identity's slot bindings denote: which code, or set of codes, a client asking for a given binding is asking for. `python/spec-kit/src/dfo_spec_kit/identity/core.py` implements spec1 end to end — strict parsing, schema validation, normalisation, projection, canonicalisation, minting — and its own module docstring already states that the vector suite wins over its prose where the two disagree. Neither that module nor the vector suite decides what a binding denotes; this document does.

**Document version:** `1.1.0` (see "Document versioning" below). This number is never hashed into any byte sequence spec1 describes.

## Precedence

`vectors/identity/*.json` (see `schemas/vectors/README.md` for the record format and the coverage plan) is spec1's executable specification for bytes and ids: for any given input, what `canonical_bytes` and `spec_id` a conforming implementation must produce, and which inputs must refuse under which `failure_category`. Where this document and the vector suite disagree on a byte sequence or a refusal, **the vector suite wins**, the same precedence `docs/frame-grammar-v1.md` states for its own vectors.

A vector cannot pin what a binding _denotes_ — the set of codes a client asking for it should receive — because denotation is a fact about retrieval and export, not about the bytes or refusal a single spec1 document mints. This document is the sole normative source for that fact. `schemas/vectors/README.md`'s own "Adding a vector" step says the vector suite "makes decided semantics executable" and "does not decide them"; this document is where spec1's binding-state semantics are decided.

## Binding states and their denotation

A slot binding declares one of four states. Three are wire states the identity core builds from an input document (`value`, `set`, `unbound`); `exact` joins them as of this document's `1.0.0`. Each denotes a set of codes:

- **`unbound`** denotes any value, or the absence of one: a slot in this state places no constraint on the property it binds.
- **`value(ref)`** denotes `ref` together with every code beneath it in the code system's declared hierarchy — `ref`'s full subtree. Where the code system is flat, or `ref` is itself a leaf with no descendants, that subtree is `ref` alone, and `value(ref)` denotes exactly the one code.
- **`exact(ref)`** denotes `ref` alone, never its descendants, regardless of whether `ref` is a leaf or an interior code. This is the deliberate parent-only ask: a client that wants only the rows recorded directly against a non-leaf code, not its subtree, states `exact`, not `value`.
- **`set(refs)`** denotes the union of `value(ref)` for each `ref` in `refs`: every member's own code plus its subtree. A `set` with one member is the same denotation as `value` on that member, so the identity core collapses a singleton set to `value` before hashing (`_build_slot_binding`, the `set` arm); that collapse is unaffected by this document, since a one-member union and a `value` denotation over the same code are one denotation under either name.

A hierarchical code's subtree, for the purpose of the `value` and `set` denotations above, is defined by the code system's own declared parent/child relation — the same hierarchy a client browsing or resolving that code system already navigates. This document does not itself enumerate any code system's hierarchy; it states only that `value` and `set` read through it, and that `exact` does not.

## `exact(L)` and `value(L)` on a leaf: distinct identities, one denotation

For a leaf code `L` (one with no descendants), `exact(L)` and `value(L)` denote the identical set of codes — `L` alone — but they are two distinct spec1 identities, because `_build_slot_binding` preserves each state's own `ref` rather than folding one state into the other: `spec_id_from_value` on an otherwise-identical document differs by binding state alone. This is the same shape of divergence the vector suite already pins elsewhere: `binding-states-all-unbound-retained` and `binding-states-no-slots-declared-diverges-from-all-unbound` share shape, entities, and window and differ only in whether two unbound slots are declared at all, and still mint two different ids. `binding-states-exact` and `binding-states-value-diverges-from-exact` pin the `exact`/`value` case the same way: same shape, entities, and window, the same single `ref`, differing only in binding state, minting two different ids.

Two identities minting from one denotation is not a defect in either the identity scheme or in an export door that reads both through their shared denotation: `spec_id` names a request as asked, not the set of codes it resolves to, and two different requests are free to resolve to the same set of codes without becoming the same request.

## Slot refs are bare dcids

A slot binding's `ref` (on `value`, each member of a `set`'s `refs`, and `exact`'s `ref`) is a bare dcid, NFC-normalised, under the identical dcid grammar `_check_dcid_grammar` already enforced for `shape`'s own dcid members: non-empty, within the string-length cap, no whitespace, no Unicode control or format character, and no `:` — a `dcid:`-prefixed spelling refuses rather than being silently stripped, since silently stripping a prefix a caller did not intend would mint an identity for a code the caller never named. `dcid-grammar-colon-prefixed-slot-ref-rejected` pins this for a slot ref the same way `dcid-grammar-colon-prefixed-rejected` already pins it for a shape dcid.

## A null facet ref

On the `measurementMethod` and `unit` axes, a facet's `value` binding may carry `"ref": null`. It denotes the facets that declare no value on that axis, the ones whose metadata omits `measurementMethod` or `unit`, as Data Commons omits the member when a facet declares none. An unbound axis admits every facet, while `value(null)` admits only the facets without a value, so the two are different asks and mint different ids: `facet-null-ref-measurement-method` differs from the common minimal fixture by that one binding. `facet-null-ref-unit` pins the unit axis.

The null ref is a single-valued facet ask and nothing wider. A `provenance` binding refuses it, since every facet carries a provenance and a null one names nothing. A facet `set` binding refuses a null member, and a slot binding refuses a null ref. `facet-null-ref-provenance-rejected`, `facet-set-null-member-rejected` and `slot-null-ref-rejected` pin these refusals.

## Documents minted before this revision

No `spec_id` moves under this revision. `identity_version` is hashed into the canonical bytes spec1 mints, so a version bump to `identity_version` itself would rotate every id in existence; this revision does not bump it, and every document stored before this revision still verifies against its own id under the rule stated above.

The null facet ref moves no id either: the identity core refused a null ref before it, so no stored document carries one.

The `value` rule records what a producer already meant by a `value` binding, rather than changing what one denotes. A producer that mints an identity from a slot's value only after a retrieval step has already widened that value through the code system's hierarchy, a door that reads a posted `value` binding back through that same descendant walk before any other processing, and a prompt that offers a covering root on the premise that a bound value on a hierarchical system widens to everything beneath it, are all already reading `value` this way. This document states the rule those producers already applied; it asks nothing new of the point where a `value` binding is minted.

The reading this supersedes — `value` on a hierarchical code denoting the bound code alone — existed at one consumer, on the export side only, and is the divergence this document closes.

Where a consumer's materialised output changes membership as a result of reading `value` under this rule rather than the superseded one, that change surfaces through the consumer's own content-versioning, not through identity: `spec_id` names the request asked, not the set of codes a given reading of it resolved to at a given time, and a versioned snapshot of a previous materialisation stays servable under its own version.

## Scope

This document states binding-state denotation, the slot-ref dcid grammar and the null facet ref; it decides nothing else about spec1. Projection, JCS canonicalisation, the structural and schema limits, and every other refusal category remain specified by `core.py`'s own implementation and by the vector suite, exactly as `schemas/vectors/README.md` and `core.py`'s module docstring already state, until a future revision of this document extends its scope to cover them.

Two related gaps are named, not fixed, here. Entity dcids (`entities`'s role values) carry the same bare-dcid convention as slot refs but are not yet run through `_check_dcid_grammar`; closing that gap is out of scope for this revision. And a code system's own declared hierarchy — the parent/child relation `value` and `set` read through — is graph data this document does not carry and cannot pin with a fixture; a reader resolving that hierarchy is a graph-dependent concern the vector suite already excludes for the same reason it excludes alias resolution and role validity (`schemas/vectors/README.md`, "Scope").

## Document versioning

This document's own version number follows semantic versioning with this meaning pinned to each component:

- **Major** — a change to what an existing binding state denotes, or the removal of a binding state. Changing what `value` on a hierarchical code denotes (as this `1.0.0` did, relative to the silence that preceded it) is exactly this kind of change.
- **Minor** — an additive change: a new binding state, or a new fact this document states about an existing one, that does not alter any already-shipped state's denotation. Adding `exact` alongside `value`, `set`, and `unbound` is this kind of change.
- **Patch** — a document-only revision: a clarified sentence, a corrected example, a typo fix. Never changes what any binding state denotes.

This version number is never hashed into any spec1 byte sequence. It travels alongside `core.py` and the vector suite purely as a caller-facing label of which revision of this document described the denotation rule a given identity's readers applied.
