# Frame grammar (v1)

This document specifies the byte-level grammar for a materialised frame and for `content_version`, the identity token that names one materialised result plus the lineage that produced it. It is written so that a second implementation, in a different language, can reproduce every byte this specification describes without reading any particular implementation's source.

**Document version:** `1.0.0` (see "Document versioning" below). This number is never hashed into any byte sequence this document describes.

## Precedence

`python/frame-grammar/src/dfo_frame_grammar/vectors/frame/` publishes a golden vector suite: one JSON record per case, each carrying an input frame (or preimage inputs) and its exact expected output bytes and tokens. Where this document and the vector suite disagree, **the vector suite wins**, and the disagreement is a release blocker — a defect in this document's prose, not in the vectors. See `python/frame-grammar/src/dfo_frame_grammar/vectors/frame/README.md` for the vector record format.

Every vector's expected output is captured from this repository's own implementation, not from an independent second implementation of this document. The suite is therefore a regression pin — proof that this implementation's output has not silently drifted from a previously recorded value — rather than independent conformance evidence that this document itself is a correct, sufficient specification for a second implementation to reproduce. That independent conformance evidence waits on a second implementation, in a different language, producing the identical bytes and tokens from this document alone.

## Canonicalisation primitive

Every byte sequence this document defines is produced by JSON Canonicalization Scheme (JCS), RFC 8785, applied to a closed JSON document. JCS fixes object member ordering (lexicographic order of UTF-16 code units over each member name), number serialisation (the ECMAScript `Number::toString` algorithm), and string escaping. This document does not restate those rules; conform to RFC 8785 directly for anything it already specifies. What this document adds on top of JCS, because JCS itself does not define them, are: which JSON document to build in the first place, and how to order the small number of arrays whose element order the document assigns program-level meaning (rows, `source_revisions`, `facets`, collection members) — JCS sorts object keys, never array elements.

All Unicode text within a canonicalised document — every string cell, every column name, every string member value — is normalised to NFC (Unicode Normalization Form C) before it enters the document. Two byte-distinct strings that NFC-normalise to the same string are the same value everywhere this grammar compares or sorts strings. A string containing an unpaired UTF-16 surrogate code point is invalid input and must be refused before it reaches canonicalisation; it cannot round-trip through UTF-8, so no valid canonical byte sequence can ever contain one.

The one collation this grammar uses, everywhere it sorts or compares strings, is Unicode code point order of each string's NFC-normalised form.

## The frame

A materialised frame is a long-form observation table: one row per observation. Its canonical byte form is the JCS serialisation of this closed JSON document:

```json
{
  "frame_version": "frame1",
  "columns": ["...", "..."],
  "rows": [
    ["...", "..."],
    ["...", "..."]
  ]
}
```

`frame_version` is a fixed string literal, `"frame1"`, the domain separator for this byte-rule generation. It is a different token from this document's own version number (above) and never changes for a document-only revision to this specification — only a change to the byte rules themselves, in a future generation of this grammar, changes it. No tabular byte form (CSV or otherwise) is ever canonical for a frame: nothing parses these bytes back, since every rendering is produced by serialising a frame's own in-memory representation, not by parsing the canonical bytes, and a tabular form has no native way to distinguish a `null` cell from an empty string, a distinction the `status` column (below) depends on.

An empty frame — zero rows — is a completely valid frame. Its canonical document is `{"frame_version": "frame1", "columns": [...], "rows": []}`, and it mints a `content_version` exactly like any other frame. Its canonical bytes differ from every non-empty frame built from the same columns, because `rows` differs.

### Column order

`columns` lists column names in the exact order below, and every row in `rows` carries its cells in that same positional order:

1. `stat_var` — the resolved statistical variable's canonical reference, in `dcid:`-prefixed spelling. Always a reference, never a display label.
1. One column per entity role the frame's shape declares (for example `origin`, `destination`, `observationAbout`). Each column's name is the role property's reference, verbatim, in its NFC-normalised form. These columns are ordered by the platform's one collation (Unicode code point order of the NFC form) applied to the role names themselves — there is no other fixed order for roles, since a shape may declare any subset. Two distinct raw role names that collide after NFC normalisation are invalid input and must be refused, since silently merging them would discard one role's data with no trace.
1. `time_period`
1. `value`
1. `unit`
1. Zero or more facet-identity columns, one per non-unit facet axis the platform's closed facet axis vocabulary declares. At this grammar version that vocabulary is `measurementMethod` and `provenance`, and — because the vocabulary is closed and small — they appear in that same fixed order, `measurementMethod` then `provenance` (which is also this pair's own code point order, so the two orderings happen to coincide; the ordering rule is the collation, not a hand-maintained list). The unit axis has no facet-identity column of its own: the `unit` column (position 5) already carries that axis's per-row identity.
1. `status` — reserved, always last.

A frame's declared role set is fixed at construction. Two frames whose role sets differ have different `columns` arrays and are not directly comparable as the same frame shape.

A declared role name equal to any reserved column name (`stat_var`, `time_period`, `value`, `unit`, `measurementMethod`, `provenance`, `status`) is invalid input and must be refused: it would derive a `columns` array carrying that name in two positions, which any consumer that pairs a cell with its column by name (rather than by position) would then collapse into one.

### Cell domains

- **`stat_var`, role columns:** non-empty strings, NFC-normalised, no lone surrogates. Entity and StatVar references, never display labels.
- **`time_period`:** a string, never a JSON number, holding either one period point or an ISO 8601 interval of two points. A point takes exactly one of three zero-padded forms: `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`. An interval is `<start>/<end>`: two points joined by a single `/`, both at the same grain, with `start` strictly before `end` (for example `2018/2022`, `2018-01/2022-12`, or `2018-01-01/2022-12-31`). A window of one period is written as the point, so `2018/2018` is invalid input and each period has one spelling. Every other interval spelling is invalid input and must be refused: an open end (`2018/`, `/2022`, `2018/..`), a duration (`2018/P5Y`), the `--` separator (`2018--2022`), ends of different grains (`2018/2022-12`), and a start after its end (`2022/2018`). Because both ends share one grain and are zero-padded, comparing them by code point orders them by date. Every digit is an ASCII digit (`0`–`9`); a decimal digit from any other Unicode script is invalid input and must be refused, since NFC normalisation does not fold it to ASCII and it would otherwise mint a distinct token for a noncanonical spelling of the same period. `MM` is `01`–`12`; `DD` is `01`–`31`. The period is carried at whatever granularity the source declared it at and is not normalised to a different granularity; this grammar does not further validate that a day exists within its own month (for example, `2021-02-30` is syntactically well-formed by this grammar's own rule, since the source itself is the authority on what it published, not this grammar). A bare year cannot be a JSON number here: JSON numbers have no native zero-padding and no way to spell a month or day, so this column is a string unconditionally.
- **`value`:** a finite IEEE-754 double, serialised through the JCS/ECMAScript number lexical form and by no other numeric formatting path. `NaN` and `±Infinity` are invalid input and must be refused before canonicalisation. An integer-valued double is spelled without a decimal point (JCS/ES6 integer spelling); `-0` collapses to `0`; exponent notation appears exactly at the ES6 thresholds JCS's own serialisation defines. A value supplied as an integer type and the identical value supplied as a floating-point type must converge to the same IEEE-754 double before serialisation, so the two spellings of one observed value produce byte-identical cells.
- **`unit`:** one of three forms:
  - `null` — the graph declares no unit for this observation.
  - a non-empty string — the canonical graph reference for the unit, NFC-normalised, no lone surrogates.
  - a unit-request object, for a frame produced by a unit conversion: a closed JSON object with members drawn only from `{"currency", "prices", "base_year"}`. `currency` is a required, upper-case, three-letter ISO 4217 code — always a concrete currency resolved per row. A frame's own row is always already resolved to one currency; the role-shaped form available under `content_version` below appears only on a content_version facet `ref`. `prices` is a required string, either `"current"` or `"constant"`. `base_year` is a required integer when `prices` is `"constant"`, and must be absent when `prices` is `"current"` — a `base_year` paired with `"current"` prices, or a `"constant"` prices with no `base_year`, is invalid input. `base_year`'s magnitude must not exceed 2⁵³ − 1, the largest integer JCS can serialise exactly; an integer beyond that bound is invalid input and must be refused, since no canonical byte sequence could ever carry it.
- **Facet-identity columns (`measurementMethod`, `provenance`):** `null` where the row's shape carries no bound reference on that axis, otherwise a non-empty string: the axis's resolved canonical reference, NFC-normalised, no lone surrogates. These columns never carry a store-side dispatch identifier for a facet — only the resolved canonical reference itself, because a canonical reference is stable across re-imports while a store-side dispatch identifier is not, and this grammar's whole purpose is byte stability across re-import.
- **`status`:** JSON `null`, in every row, at this grammar version. `null` is deliberate and is not interchangeable with an empty string: `null` means "no qualification recorded"; an empty string would itself be a recorded (if degenerate) value. This column is reserved for a future minor version of this grammar to populate with a real qualification; see "The reserved `status` column" below for why it ships now, unused, rather than being added later.

### Missing and suppressed rows

A time period inside a requested window for which no observation exists produces no row at all — there is no sentinel row standing in for "missing." At this grammar version, an observation whose value is suppressed by its source is likewise represented by the complete absence of a row: it has no representation in the canonical bytes until a later minor version of this grammar populates the `status` column with a real suppression qualification. A consumer cannot distinguish "missing because never observed" from "suppressed" from these bytes alone at this grammar version.

### Row order and uniqueness

Before canonicalisation, rows are sorted by a key built from every column **except `value` and `status`**, in column order: `(stat_var, role cells in column order, time_period, unit, measurementMethod, provenance)`. `value` is excluded because a row's own observed number is not part of what identifies its grain — two rows differing only in `value` are not two distinct observations, they are a conflict (see below). `status` is excluded for the same reason, reserved for the day it starts to vary.

Comparing one cell against another of the same column, for sort purposes, follows these rules, in order of applicability:

1. `null` sorts before every string and every object.
1. A string compares against another string by Unicode code point order of each string's NFC-normalised form (the platform's one collation).
1. An object (only ever the `unit` column's unit-request form) compares against another object by comparing the JCS serialisation of each object as a byte string. Because JCS output is valid UTF-8 and UTF-8's byte ordering agrees with Unicode code point ordering for valid UTF-8, this is the same collation applied one level removed.

`null`, strings, and objects are never compared against each other outside this ranking; a `unit` column holding `null` in one row and a string in another is ordered by rule 1 or 2 respectively, never by attempting a cross-type comparison.

**This sort key must be unique across a frame's rows.** Two rows that produce the identical key — that is, two rows agreeing on every cell except possibly `value` and `status` — describe the same observational grain twice, and this is invalid input: a frame construction with a duplicate key must refuse rather than pick a winner or silently keep both. This grammar takes no position on why an upstream duplicate might occur; it only requires the frame layer to fail closed on one, since silently tie-breaking would either invent an ordering with no source authority behind it, or let a corrupt duplicate slip past the identity boundary into some later roll-up.

### The reserved `status` column

`status` is present, named `status`, in the last column position, holding JSON `null` in every row, starting at this grammar's very first version — not added once there is a real qualification to record. The reason is byte stability under an additive change: if this column did not exist today and were added by a later minor grammar version, `columns` would gain a member, every row would gain a cell, the canonical bytes for every already-materialised frame would change the moment it was re-canonicalised, and every `content_version` derived from those bytes would rotate — for a change that adds no new information to any frame that never populates the new column. Reserving the column now, always `null`, means a later minor version that starts populating it for some rows changes bytes only for the frames that actually gain a non-null value; every frame that continues to see `status` stay `null` in every row is byte-identical before and after that minor version ships.

## `content_version`

`content_version` names one logical materialised result: a specific frame's bytes, plus the lineage that produced them. Two distinct requests that resolve to the identical frame under identical lineage mint the identical `content_version`, regardless of anything else that differs between the two requests (their originating spec, for instance — see "Pinned exclusions" below).

Its preimage is the JCS serialisation of this closed JSON document:

```json
{
  "identity_version": "cv1",
  "frame_sha256": "<64 lowercase hex characters>",
  "graph_revision": "...",
  "source_revisions": [{"source": "...", "revision": "..."}, ...],
  "facets": [{"axis": "...", "ref": "...", "basis": "bound|declared_default"}, ...],
  "conversion": {"recipe": "...", "rate_table_version": "...", "deflator_table_version": "..."}
}
```

`identity_version` is the fixed string `"cv1"`, this preimage family's own domain separator — distinct from `frame_version` (`"frame1"`, the frame bytes' own domain separator) and from `colcv1` (the collection-level preimage's domain separator, below), so that no two of these three JSON-document families can ever collide on the same bytes by construction.

Member by member:

- **`frame_sha256`** — the full 64-character lowercase hexadecimal SHA-256 digest of the frame's own canonical bytes (the JCS output described above). This is a nested digest, not the frame embedded inline: embedding the frame directly would make it impossible to publish a fixed-size vector for this preimage, and would cost a second full pass over the frame's bytes for no benefit. The digest is the full 64 hex characters here — truncation to a shorter form only ever happens at the public token stage (below), never inside a preimage. A value that is not exactly 64 lowercase hex characters — a shorter public token's own digest slice, for instance — is invalid input and must be refused before it enters the preimage.
- **`graph_revision`** — an opaque string naming the graph revision the materialisation read from, carried verbatim except for NFC normalisation. Never parsed or interpreted as anything other than an opaque identifier.
- **`source_revisions`** — an array of `{"source": "...", "revision": "..."}` objects, one per upstream source the materialisation read, both members opaque strings carried verbatim except for NFC normalisation. Because JCS does not order array elements, this document pins the array's own order: sorted by `source`, by the platform's one collation. A caller supplying the same set of source/revision pairs in a different order mints the identical `content_version`. Two entries sharing the same `source` are invalid input and must be refused rather than tie-broken by the sort: one materialisation reads one revision per source, so a shared `source` is contradictory, not two values this document could order either way and still call the set order-independent.
- **`facets`** — an array of `{"axis": "...", "ref": "...", "basis": "bound"|"declared_default"}` objects, one per facet axis actually resolved for this materialisation (including the `unit` axis, present here where the frame's facet-identity columns omit it because the frame's `unit` column already carries that axis's identity). `axis` is the axis name, NFC-normalised. `ref` is either a plain string (the axis's resolved canonical reference) or, for the `unit` axis under a conversion, the same closed unit-request object the frame's own `unit` cell can carry, with one difference: this `ref` form's `currency` member may additionally be a role object, `{"role": "observationAbout" | "origin" | "destination"}`, naming one of the spec's entity role keys rather than a currency already resolved per row — the shape a _bound_ request carries before per-row resolution, mirroring the identical role shape `dfo_spec_kit`'s own unit-request identity accepts. The frame's own `unit` cell never carries this role form, since a materialised row is always already resolved to one concrete currency; only this preimage's own `facets` entry, which digests the request as bound, can. This object form (either currency spelling) is valid only on the `unit` axis; the identical shape supplied as `ref` for any other axis is invalid input and must be refused, since no other axis has a request form of its own for that object to represent. `basis` is exactly one of two permanent literal spellings: `"bound"` when the request itself bound this axis, `"declared_default"` when no request-level binding existed and the axis fell back to the shape's own declared default. This array, too, is sorted by `axis` in this document's own pinned order (JCS does not order it), by the platform's one collation, so member input order never affects the minted token. Two entries sharing the same `axis` are invalid input and must be refused rather than tie-broken by the sort, for the same reason as `source_revisions` above: one materialisation makes one facet selection per axis.
- **`conversion`** — **omitted from the document entirely when no conversion recipe ran.** This is the only valid spelling of "no conversion happened" — an explicit JSON `null` in this member's place is invalid input and must be refused, so that "omitted" and "explicitly null" can never both exist as two different spellings of the same fact that would otherwise hash to two different tokens. When a recipe did run, this member is a closed object: `recipe` and `rate_table_version` (both required), and `deflator_table_version`, present if and only if the recipe's own join pulled in a deflator table, absent otherwise. A conversion recipe is a graph-declared fact, not something this grammar derives: the graph names, per StatVar or unit, which rate series it joins, whether that series is read as a period average or an end-of-period reading, which deflator (if any) it applies, and which base year a constant-price result rebases to; `recipe` is that declaration's own opaque identifying string, NFC-normalised, no lone surrogates, carried verbatim and never parsed or interpreted by this grammar — the identical treatment `graph_revision` gets above. Two differently-named recipes mint two different `content_version`s even if they happen to compute identical numbers by coincidence, because this member's identity is which declared recipe ran, not what it produced. `rate_table_version` and `deflator_table_version` are opaque strings under the same verbatim, NFC-normalised treatment, identifying which version of the rate table, respectively the deflator table, the recipe's join read from — the same discipline a `source_revisions` entry's own `revision` member follows, and for the same reason: the reference data a recipe joins against can be replaced by a newer snapshot without the recipe's own name changing, so a `content_version` distinguishes "the same recipe, run against an updated rate table" from "the same recipe, run against the table that was live when this frame was first materialised" only because the table version travels as its own member, separately from `recipe`. The canonical unit request form a conversion answers (`currency`, `prices`, `base_year`, "Cell domains" above) already canonicalises into this preimage's own `facets` member, as the `unit` axis's `ref` (member by member, above); `conversion` names only the recipe and table versions that satisfied that request, not the request's own shape a second time. Adding, removing, or changing any member of a present `conversion` record moves `content_version`, the same as any other preimage member; a `content_version` minted while `conversion` is omitted is unaffected by any of this, since the member stays absent from the preimage whenever no recipe ran. A manifest trace records conversion as an array of entries, one per recipe and table-version pair, so a minter also accepts that array as its `conversion` input. A frame converts under one pair, so its trace holds exactly one entry; an array of any other length, empty included, is invalid input and must be refused rather than picked from or merged. The entry carries the same `recipe`, `rate_table_version`, and optional `deflator_table_version`, plus three trace-only members: `members` (the converted StatVars with their native units), `fallback`, and `projected` (counts with capped examples). The minter takes the three record members into this preimage object and drops the trace-only ones, so a one-entry array and the bare record mint the same token, and a trace-only member never moves it. An entry member outside those six is invalid input and must be refused. The trace-only members are valid only inside an array entry; the bare record stays the closed three-member object.

The member set above is closed and exhaustive: nothing else can appear in a `cv1` preimage. In particular, none of the following are ever hashed into a `content_version`, and every one of them is deliberately excluded, not merely absent by omission of a feature:

- **`spec_id`** — the identifier of the request definition that produced this materialisation. Two distinct definitions serving the identical frame under identical lineage must mint the identical `content_version`; the pairing `(spec_id, content_version)` is what names a specific logical result, and that pairing is only meaningful if `content_version` on its own never varies with `spec_id`.
- **`frame_grammar_version`** — this document's own version number (see "Document versioning"). A change to the byte rules themselves already moves `content_version` through `frame_sha256`, because the frame's bytes actually changed; a document-only revision, or an additive minor revision that changes no existing frame's bytes (the `status` column populating on some newly-exercised condition, say), must leave every already-minted `content_version` standing still. Folding this version number into the preimage directly would defeat that guarantee.
- **`applied_window`**, coverage information, **`materialiser_build`**, default-policy identifiers or versions, **`selection_reason`**, and any store-side **dispatch facet identifier** — none of these describe the materialised result or the lineage that produced it; they describe how a particular request arrived at that result, or which build produced it, or bookkeeping a wider manifest trace needs for its own purposes. A `materialiser_build` in particular must never enter this preimage: a deploy of the exact same code should never, by itself, rotate every `content_version` in a system.

### Public token

The public `content_version` token is the fixed prefix `cv_` followed by the first 32 lowercase hexadecimal characters of the SHA-256 digest of the preimage's own canonical (JCS) bytes.

### Recompute and the persistence obligation

Given a frame's own stored canonical bytes and the lineage fragment that produced its `content_version` (`graph_revision`, `source_revisions`, `facets`, and the conversion record or the trace's one-entry conversion array when present), the identical `content_version` can be recomputed independently, as an integrity check on stored data: take the SHA-256 digest of the stored bytes directly to reproduce `frame_sha256`, then rebuild and hash the `cv1` preimage exactly as above.

This recompute path hashes the bytes it is handed **as they are** — it never re-parses them back into a frame and re-serialises that frame to get a value to check. This has a direct consequence for anyone persisting a materialised frame: **the canonical frame bytes themselves must be what gets persisted, verbatim, or a lossless alternative encoding from which those exact canonical bytes can be reconstructed deterministically.** Any lossy storage transform — one that cannot reconstruct the identical canonical bytes bit-for-bit — breaks recompute silently: a single mutated stored byte must be detectable as a hash mismatch against the originally minted token, and that guarantee only holds if what recompute hashes is the same sequence of bytes that was originally canonicalised, or a value deterministically equivalent to it.

## Collection-level `content_version`

A collection of materialised results (for example, every spec inside one manifest) mints its own `content_version` over its members' own tokens. The preimage is the JCS serialisation of:

```json
{"identity_version": "colcv1", "members": [["<spec_id>", "<content_version>"], ...]}
```

`identity_version` is the fixed string `"colcv1"`, distinct from `cv1` (frame-level) so the two preimage families cannot collide. `members` is an array of two-element arrays, each `[spec_id, content_version]`, carrying each member's full public token strings verbatim. Each `spec_id` must be `spec_` followed by exactly 32 lowercase hexadecimal characters, and each `content_version` must be `cv_` followed by exactly 32 lowercase hexadecimal characters — the platform's two public token shapes, `spec_` naming a definition and `cv_` naming a materialised result; a member token outside either shape is invalid input and must be refused, not carried through unvalidated into a collection token that then looks well-formed regardless. Because JCS does not order array elements, this document pins the order here too: sorted by `spec_id`, by the platform's one collation. A caller supplying the same member set in a different order mints the identical collection-level token. Two members sharing the same `spec_id` are invalid input and must be refused rather than tie-broken by the sort: one collection has one member per spec, so a shared `spec_id` is contradictory, the same discipline `source_revisions` and `facets` apply above.

The public token uses the identical construction as the frame-level token: `cv_` followed by the first 32 lowercase hexadecimal characters of the SHA-256 digest of this preimage's own canonical bytes.

## Document versioning

This document's own version number, distinct from `frame_version` (`"frame1"`, above), follows semantic versioning with this meaning pinned to each component:

- **Major** — a byte-rule generation change: anything that changes what canonical bytes result from some frame that was already valid under the prior generation, or that changes the closed cell or column shape this document defines. `frame1` implies major `1`. Adding a fourth facet axis to the closed vocabulary this document names is a major change, taken knowingly: this grammar's row format is positional, with no sparse way to add a column, so a new axis both changes `columns` and rotates every already-minted `content_version` through `frame_sha256`.
- **Minor** — an additive change to this document that does not, on its own, change any existing frame's canonical bytes: the `status` column beginning to carry a non-null value under some newly-declared condition is the anticipated case. A frame that never exercises the new condition stays byte-identical; only a frame that does changes, and it changes because its own data changed, not because this version number changed.
- **Patch** — a document-only revision: a clarified sentence, a corrected example, a typo fix. Never changes any frame's canonical bytes.

This version number is never hashed into canonical frame bytes and never hashed into a `content_version` preimage, at either level. It travels alongside a recompute lineage fragment purely as a caller-facing label of which revision of this document described the code that produced a given frame.
