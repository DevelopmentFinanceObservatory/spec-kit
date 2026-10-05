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
`FRAME_GRAMMAR_VERSION` names the published document and moves on every
revision to it, including ones that change no byte rule at all. Collapsing
them into one token would mean a document typo fix rotates `frame_sha256`
for every stored frame, or that a genuine byte-rule change ships with no
version bump visible to a consumer watching this constant. Neither token
ever enters a `content_version` preimage (see `content_version`'s own
module docstring for why); this one is not even hashed into the frame bytes
themselves.

What each version component means, by the rule this document locks:

- **Major** -- a byte-rule generation change: anything that changes what
  bytes `canonical_frame_bytes` produces for some frame that was already
  valid under the prior generation, or that changes the closed cell/column
  shape a `Frame` accepts. `frame1` (`grammar.FRAME_VERSION`) implies major
  `1`; a hypothetical `frame2` would imply major `2`. Adding a fourth facet
  axis is a major change taken knowingly: a positional row format has no
  sparse way to add a column, so the column set changes and every
  `content_version` rotates through `frame_sha256`.
- **Minor** -- an additive change to the document that moves no existing
  frame's canonical bytes by itself: the reserved `status` column starting
  to carry a non-null value under some newly-declared condition is the
  shipped example. Every frame where that condition never fires stays
  byte-identical under the new minor version; a frame that does newly
  exercise the addition sees its bytes move because of what changed in its
  own data, not because this version number moved.
- **Patch** -- a document-only revision: prose clarification, a worked
  example, a typo fix. Never changes what `canonical_frame_bytes` produces
  for any frame, old or new.

What moves this token, and what deliberately does not
-------------------------------------------------------
Moves it: any change to `docs/frame-grammar-v1.md`, at whatever component
the change's own scope calls for above -- including a change that touches
no line of this package's code, since the document itself is the versioned
artifact.

Deliberately does not move it: nothing in this package reads or is driven
by `FRAME_GRAMMAR_VERSION`'s own value -- `canonical_frame_bytes` and
`mint_content_version` are both indifferent to it (see `content_version`'s
pinned-exclusions test, which bumps this constant alone and confirms a
recomputed `content_version` stands still). It is a label a caller can read
off `FrameTrace`, not an input any function branches on.
"""

from __future__ import annotations

FRAME_GRAMMAR_VERSION = "1.0.0"
