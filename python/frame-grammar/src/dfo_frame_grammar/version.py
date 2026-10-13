"""`FRAME_GRAMMAR_VERSION`: the published frame grammar document's own
semver -- the only one of this package's two grammar-version tokens that
reaches the wire, carried on
`dfo_frame_grammar.content_version.FrameTrace.frame_grammar_version` for a
caller's own trace and audit value.

Normative source: `docs/frame-grammar-v1.md`, whose own version-numbering
section this constant implements.

This is **not** `dfo_frame_grammar.grammar.FRAME_VERSION` ("frame1"), the
domain separator hashed into every canonical frame document (see
`dfo_frame_grammar.frame.canonical_frame_bytes`). The two are deliberately
independent tokens for two different things: `FRAME_VERSION` names the byte
rules themselves and moves only on a byte-rule generation change;
`FRAME_GRAMMAR_VERSION` names the published document and, from the
grammar's first release, moves on every revision to it, including ones
that change no byte rule at all. Collapsing
them into one token would mean a document typo fix rotates `frame_sha256`
for every stored frame, or that a genuine byte-rule change ships with no
version bump visible to a consumer watching this constant. Neither token
ever enters a `content_version` preimage (see `content_version`'s own
module docstring for why); this one is not even hashed into the frame bytes
themselves.

What each version component means, by the rule this document locks:

- **Major** -- a byte-rule generation change: anything that changes what
  bytes `canonical_frame_bytes` produces for some frame that was already
  valid under the prior generation, or that makes such a frame invalid, such
  as removing or renaming a member of a closed cell or adding a column.
  `frame1` (`grammar.FRAME_VERSION`) implies major `1`; a hypothetical
  `frame2` would imply major `2`. Adding a fourth facet axis is a major
  change taken knowingly: a positional row format has no sparse way to add a
  column, so the column set changes and every `content_version` rotates
  through `frame_sha256`.
- **Minor** -- a change after which every frame valid under the prior
  version is still valid and has the same canonical bytes. One kind is
  additive: the grammar accepts something a reader on the prior version
  refuses, such as the reserved `status` column carrying a non-null value
  under a newly-declared condition, or a new optional member in a closed
  cell. A frame that does not use the addition stays byte-identical; a frame
  that does is new data only a reader on the new version accepts, so every
  reader takes the new minor version before any producer writes the
  addition. The other kind is a new `content_version` preimage family under
  a new `identity_version` separator: frame bytes stand still and every
  token moves, and this version is what tells a token minted under one
  family from one minted under the next.
- **Patch** -- a document-only revision: prose clarification, a worked
  example, a typo fix. Never changes what `canonical_frame_bytes` produces
  for any frame, old or new.

These rules count from the grammar's first release. `1.0.0` includes the
interval form of `time_period` and the composed form of the `unit` cell,
and mints `content_version` under `cv1`. `1.1.0` mints it under `cv2`, the
hash of the frame alone, and adds the optional `recipe` member to the unit
request object, an additive minor change: a cell without `recipe`
serialises as it did under `1.0.0`, and a reader on `1.0.0` refuses a cell
that carries one.

What moves this token, and what deliberately does not
-------------------------------------------------------
Moves it, from the grammar's first release: any change to
`docs/frame-grammar-v1.md`, at whatever component the change's own scope
calls for above -- including a change that touches no line of this
package's code, since the document itself is the versioned artifact.

Deliberately does not move it: nothing in this package reads or is driven
by `FRAME_GRAMMAR_VERSION`'s own value -- `canonical_frame_bytes` and
`mint_content_version` are both indifferent to it, and neither accepts it as
a parameter. It is a label a caller can read off `FrameTrace`, not an input
any function branches on.
"""

from __future__ import annotations

FRAME_GRAMMAR_VERSION = "1.1.0"
