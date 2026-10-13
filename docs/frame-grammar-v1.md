# Frame grammar (v1)

This document specifies the byte-level grammar for a materialised frame and for `content_version`, the identity token that names one materialised frame. It is written so that a second implementation, in a different language, can reproduce every byte this specification describes without reading any particular implementation's source.

**Document version:** `1.1.0` (see "Document versioning" below). This number is never hashed into any byte sequence this document describes.

## Precedence

`python/frame-grammar/src/dfo_frame_grammar/vectors/frame/` publishes a golden vector suite: one JSON record per case, each carrying an input frame (or a frame digest) and its exact expected output bytes and tokens. Where this document and the vector suite disagree, **the vector suite wins**, and the disagreement is a release blocker — a defect in this document's prose, not in the vectors. See `python/frame-grammar/src/dfo_frame_grammar/vectors/frame/README.md` for the vector record format.

Every vector's expected output is captured from this repository's own implementation, not from an independent second implementation of this document. The suite is therefore a regression pin — proof that this implementation's output has not silently drifted from a previously recorded value — rather than independent conformance evidence that this document itself is a correct, sufficient specification for a second implementation to reproduce. That independent conformance evidence waits on a second implementation, in a different language, producing the identical bytes and tokens from this document alone.

## Canonicalisation primitive

Every byte sequence this document defines is produced by JSON Canonicalization Scheme (JCS), RFC 8785, applied to a closed JSON document. JCS fixes object member ordering (lexicographic order of UTF-16 code units over each member name), number serialisation (the ECMAScript `Number::toString` algorithm), and string escaping. This document does not restate those rules; conform to RFC 8785 directly for anything it already specifies. What this document adds on top of JCS, because JCS itself does not define them, are: which JSON document to build in the first place, and how to order the one array whose element order the document assigns program-level meaning (rows) — JCS sorts object keys, never array elements.

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
- **`unit`:** one of four forms:
  - `null` — the graph declares no unit for this observation.
  - a non-empty string — the canonical graph reference for the unit, NFC-normalised, no lone surrogates.
  - a unit-request object, for a frame produced by a unit conversion: a closed JSON object with members drawn only from `{"currency", "prices", "base_year", "recipe"}`. `currency` is a required, upper-case, three-letter ISO 4217 code — always a concrete currency resolved per row. `prices` is a required string, either `"current"` or `"constant"`. `base_year` is a required integer when `prices` is `"constant"`, and must be absent when `prices` is `"current"` — a `base_year` paired with `"current"` prices, or a `"constant"` prices with no `base_year`, is invalid input. `base_year`'s magnitude must not exceed 2⁵³ − 1, the largest integer JCS can serialise exactly; an integer beyond that bound is invalid input and must be refused, since no canonical byte sequence could ever carry it. `recipe` is optional and is the id of the conversion recipe that produced the cell, a non-empty string, NFC-normalised, no lone surrogates. Two cells that differ only in `recipe` are two units, so rows that differ only there are two grains. The unit request in a spec's identity (`docs/identity-v1.md`) is closed at `currency`, `prices` and `base_year`, because a spec asks for a unit and the recipe that delivered it belongs to the frame.
  - a composed-unit object, for a frame whose values are a quotient of two measures: a closed JSON object with exactly the members `numerator` and `denominator`, each holding one part. A part takes one of three forms: a non-empty string, the canonical graph reference for a unit, NFC-normalised, no lone surrogates; a unit-request object under the rules above; or a closed JSON object with exactly the member `stat_var`, for a side whose observations declare no unit, holding the reference of the statistical variable that side counts under the `stat_var` column's own rule (a non-empty, NFC-normalised, `dcid:`-prefixed string). `{"numerator": "USDollar", "denominator": {"stat_var": "dcid:Count_Person"}}` is US dollars per person. A part is never `null` and never itself a composed-unit object, so a composed unit is one level deep. An object carrying `numerator` or `denominator` is read as a composed unit and every other object as a unit request; any other member beside those two, a missing member, or a part outside the three forms is invalid input and must be refused.
- **Facet-identity columns (`measurementMethod`, `provenance`):** `null` where the row's shape carries no bound reference on that axis, otherwise a non-empty string: the axis's resolved canonical reference, NFC-normalised, no lone surrogates. These columns never carry a store-side dispatch identifier for a facet — only the resolved canonical reference itself, because a canonical reference is stable across re-imports while a store-side dispatch identifier is not, and this grammar's whole purpose is byte stability across re-import.
- **`status`:** JSON `null`, in every row, at this grammar version. `null` is deliberate and is not interchangeable with an empty string: `null` means "no qualification recorded"; an empty string would itself be a recorded (if degenerate) value. This column is reserved for a future minor version of this grammar to populate with a real qualification; see "The reserved `status` column" below for why it ships now, unused, rather than being added later.

### Missing and suppressed rows

A time period inside a requested window for which no observation exists produces no row at all — there is no sentinel row standing in for "missing." At this grammar version, an observation whose value is suppressed by its source is likewise represented by the complete absence of a row: it has no representation in the canonical bytes until a later minor version of this grammar populates the `status` column with a real suppression qualification. A consumer cannot distinguish "missing because never observed" from "suppressed" from these bytes alone at this grammar version.

### Row order and uniqueness

Before canonicalisation, rows are sorted by a key built from every column **except `value` and `status`**, in column order: `(stat_var, role cells in column order, time_period, unit, measurementMethod, provenance)`. `value` is excluded because a row's own observed number is not part of what identifies its grain — two rows differing only in `value` are not two distinct observations, they are a conflict (see below). `status` is excluded for the same reason, reserved for the day it starts to vary.

Comparing one cell against another of the same column, for sort purposes, follows these rules, in order of applicability:

1. `null` sorts before every string and every object.
1. A string compares against another string by Unicode code point order of each string's NFC-normalised form (the platform's one collation).
1. An object (only ever the `unit` column's unit-request or composed-unit form) compares against another object of the same form by comparing the JCS serialisation of each object as a byte string. Because JCS output is valid UTF-8 and UTF-8's byte ordering agrees with Unicode code point ordering for valid UTF-8, this is the same collation applied one level removed.
1. A unit-request object sorts before every composed-unit object.

The `unit` column therefore orders `null`, then strings, then unit-request objects, then composed-unit objects. `null`, strings, and the two object forms are never compared against each other outside this ranking; a `unit` column holding `null` in one row and a string in another is ordered by rule 1 or 2 respectively, never by attempting a cross-type comparison.

### Arrow layout of the `unit` column

The canonical bytes above are the only identity-bearing form of a frame. An implementation that also holds a frame as an Arrow table carries the `unit` column in one nullable struct, whose members consumers read by name:

```
unit: struct<
  ref: string,
  request: struct<currency: string, prices: string, base_year: int64>,
  composed: string
>
```

A column where some cell's unit request carries `recipe` takes the recipe layout, in which `request` gains a fourth child, `recipe: string`. A column where no cell carries one keeps the layout above, the one `1.0.0` defines, so a frame without a recipe stores byte for byte as it did under `1.0.0` and a `1.0.0` reader reads it. A writer that fixes its schema before it has seen every row, such as a streaming Parquet writer, takes the recipe layout only when it states that its cells may carry a recipe, and then writes every chunk in that layout.

A `null` unit is a null struct. In a non-null cell exactly one of `ref`, `request`, and `composed` is non-null. `base_year`, and `recipe` in the recipe layout, are null where the JSON object omits them. `composed` holds the composed unit's JSON object serialised under JCS as UTF-8 text, the same text the cell has in the canonical bytes, so equal composed units hold equal strings. Frame construction from a table reads any JSON text of a composed unit in `composed` and stores its JCS form. It refuses text that is not JSON, repeats a member name, or holds anything but a composed unit with `invalid_unit`, and a `composed` member whose Arrow type is neither a string type nor null with `schema_violation`. A table that carries the column as the two-member struct `struct<ref, request>` holds no composed unit. It and both three-member layouts are valid input to frame construction, which gives the frame the recipe layout exactly when some cell carries a recipe.

**This sort key must be unique across a frame's rows.** Two rows that produce the identical key — that is, two rows agreeing on every cell except possibly `value` and `status` — describe the same observational grain twice, and this is invalid input: a frame construction with a duplicate key must refuse rather than pick a winner or silently keep both. This grammar takes no position on why an upstream duplicate might occur; it only requires the frame layer to fail closed on one, since silently tie-breaking would either invent an ordering with no source authority behind it, or let a corrupt duplicate slip past the identity boundary into some later roll-up.

### The reserved `status` column

`status` is present, named `status`, in the last column position, holding JSON `null` in every row, starting at this grammar's very first version — not added once there is a real qualification to record. The reason is byte stability under an additive change: if this column did not exist today and were added by a later minor grammar version, `columns` would gain a member, every row would gain a cell, the canonical bytes for every already-materialised frame would change the moment it was re-canonicalised, and every `content_version` derived from those bytes would rotate — for a change that adds no new information to any frame that never populates the new column. Reserving the column now, always `null`, means a later minor version that starts populating it for some rows changes bytes only for the frames that actually gain a non-null value; every frame that continues to see `status` stay `null` in every row is byte-identical before and after that minor version ships.

## `content_version`

`content_version` names one logical materialised frame. Two materialisations that produce the identical frame mint the identical `content_version`, whatever lineage produced them and whatever else differs between the two requests (see "Pinned exclusions" below).

Its preimage is the JCS serialisation of this closed JSON document:

```json
{
  "identity_version": "cv2",
  "frame_sha256": "<64 lowercase hex characters>"
}
```

`identity_version` is the fixed string `"cv2"`, this preimage family's own domain separator. It is distinct from `frame_version` (`"frame1"`, the frame bytes' own domain separator), so the two JSON-document families cannot collide on the same bytes by construction. It is also distinct from `"cv1"`, the retired family whose preimage hashed the materialisation's lineage beside the frame digest, so a `cv2` token never equals a `cv1` token minted over a different preimage.

`frame_sha256` is the full 64-character lowercase hexadecimal SHA-256 digest of the frame's own canonical bytes (the JCS output described above). This is a nested digest, not the frame embedded inline: embedding the frame directly would make it impossible to publish a fixed-size vector for this preimage, and would cost a second full pass over the frame's bytes for no benefit. The digest is the full 64 hex characters here — truncation to a shorter form only ever happens at the public token stage (below), never inside a preimage. A value that is not exactly 64 lowercase hex characters — a shorter public token's own digest slice, for instance — is invalid input and must be refused before it enters the preimage.

### Pinned exclusions

The member set above is closed and exhaustive: nothing else can appear in a `cv2` preimage. In particular, none of the following are ever hashed into a `content_version`, and every one of them is deliberately excluded, not merely absent by omission of a feature:

- **Lineage**: the graph revision and per-source revisions a materialisation read, the facet selections it made and whether each was bound or a declared default, and any conversion recipe with the rate and deflator table versions it joined. Two lineages that produce the identical frame name one logical result, so they share one token. A recipe id a producer writes into a unit cell's `recipe` member is part of the frame, so it reaches the token through `frame_sha256`. A caller that needs lineage records it beside the token, per materialisation.
- **`spec_id`**: the identifier of the request definition that produced this materialisation. Two distinct definitions serving the identical frame must mint the identical `content_version`; the pairing `(spec_id, content_version)` is what names a specific logical result, and that pairing is only meaningful if `content_version` on its own never varies with `spec_id`.
- **`frame_grammar_version`**: this document's own version number (see "Document versioning"). A change to the byte rules themselves already moves `content_version` through `frame_sha256`, because the frame's bytes changed; a document-only revision, or an additive minor revision that changes no existing frame's bytes, must leave every already-minted `content_version` standing still.
- **`applied_window`**, coverage information, **`materialiser_build`**, default-policy identifiers or versions, **`selection_reason`**, and any store-side **dispatch facet identifier**: none of these describe the materialised frame; they describe how a particular request arrived at it, which build produced it, or bookkeeping a wider manifest trace needs for its own purposes. A `materialiser_build` in particular must never enter this preimage: a deploy of the exact same code should never, by itself, rotate every `content_version` in a system.

### Public token

The public `content_version` token is the fixed prefix `cv_` followed by the first 32 lowercase hexadecimal characters of the SHA-256 digest of the preimage's own canonical (JCS) bytes.

### Recompute and the persistence obligation

Given a frame's own stored canonical bytes, the identical `content_version` can be recomputed independently, as an integrity check on stored data: take the SHA-256 digest of the stored bytes directly to reproduce `frame_sha256`, then build and hash the `cv2` preimage exactly as above.

This recompute path hashes the bytes it is handed **as they are** — it never re-parses them back into a frame and re-serialises that frame to get a value to check. This has a direct consequence for anyone persisting a materialised frame: **the canonical frame bytes themselves must be what gets persisted, verbatim, or a lossless alternative encoding from which those exact canonical bytes can be reconstructed deterministically.** Any lossy storage transform — one that cannot reconstruct the identical canonical bytes bit-for-bit — breaks recompute silently: a single mutated stored byte must be detectable as a hash mismatch against the originally minted token, and that guarantee only holds if what recompute hashes is the same sequence of bytes that was originally canonicalised, or a value deterministically equivalent to it.

## Document versioning

This document's own version number, distinct from `frame_version` (`"frame1"`, above), follows semantic versioning with this meaning pinned to each component:

- **Major** — a byte-rule generation change: anything that changes what canonical bytes result from some frame that was already valid under the prior generation, or that makes such a frame invalid, such as removing or renaming a member of a closed cell or adding a column. `frame1` implies major `1`. Adding a fourth facet axis to the closed vocabulary this document names is a major change, taken knowingly: this grammar's row format is positional, with no sparse way to add a column, so a new axis both changes `columns` and rotates every already-minted `content_version` through `frame_sha256`.
- **Minor** — a change after which every frame valid under the prior version is still valid and has the same canonical bytes. One kind is additive: the grammar accepts something a reader on the prior version refuses, such as the `status` column carrying a non-null value under a newly-declared condition, or a new optional member in a closed cell. A frame that does not use the addition stays byte-identical. A frame that does use it is new data that only a reader on the new version accepts, so every reader takes the new minor version before any producer writes the addition. The other kind is a new `content_version` preimage family under a new `identity_version` separator: every frame's bytes stand still and every token moves, and this version number is what tells a token minted under one family from one minted under the next.
- **Patch** — a document-only revision: a clarified sentence, a corrected example, a typo fix. Never changes any frame's canonical bytes.

These rules count from this grammar's first release. `1.0.0` includes the interval form of `time_period` and the composed form of the `unit` cell, and mints `content_version` under `cv1`. `1.1.0` mints it under `cv2`, the hash of the frame alone, and adds the optional `recipe` member to the unit-request object, an additive minor change: a cell without `recipe` serialises as it did under `1.0.0`, and a reader on `1.0.0` refuses a cell that carries one.

This version number is never hashed into canonical frame bytes and never hashed into a `content_version` preimage, at either level. It travels alongside a materialisation's lineage purely as a caller-facing label of which revision of this document described the code that produced a given frame.
