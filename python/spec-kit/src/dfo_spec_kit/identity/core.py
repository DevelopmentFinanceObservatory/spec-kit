"""The canonical implementation of the spec1 identity scheme: parse,
validate, normalise, canonicalise, mint.

The identity vector suite (`vectors/identity/*.json`; see
`schemas/vectors/README.md` for the record format and the frozen
failure-category vocabulary) is this scheme's executable specification.
Where this module and the vectors disagree, the vectors win until the
disagreement is escalated and resolved upstream.

Pure module: stdlib plus the `rfc8785` PyPI package only. No import from
anything else in this package, so it stays usable standalone.

The pipeline, end to end:

    strict I-JSON parse (bytes)  -- parse_identity_bytes
        -> identity_version gate + schema validation -- validate_identity_core
        -> normalisation + projection build          -- build_projection
        -> JCS (RFC 8785) serialisation               -- canonicalize
        -> spec_id = "spec_" + first 32 lowercase hex of SHA-256(bytes)
                                                        -- mint_spec_id

`spec_id_from_bytes` and `spec_id_from_value` run the whole pipeline from
either entry point. Every published input limit is enforced, raising
`input_limit_exceeded`. The structural limits (max definition bytes, nesting
depth, string length, number length) are checked at the strict-parse stage,
ahead of schema validation: max bytes and max number length are token-level
and only observable on the bytes path (a definition's byte size and a number
literal's exact spelling do not survive parsing), while nesting depth and
string length are checked on both entry points, since both are properties of
the parsed value itself. On the bytes path, nesting depth is checked twice:
a raw-bytes pre-scan ahead of `json.loads` bounds depth before the decoder's
own per-level recursion can exhaust the interpreter's recursion limit, then
the ordinary value-level check re-confirms it on the parsed result, same as
the value-path entry point. The schema limits (max slots, entity roles,
values per role, set members, facet axes) are checked at schema-validation
time, on the input as written, before de-duplication.

Every walk over a parsed JSON value in this module (nesting depth, lone
surrogates, string length) is iterative, not recursive: a value handed in
directly on the value path never passed through `json.loads`, so nothing
upstream bounds how deep it can be, and Python's own recursion limit is far
lower than the depths a malicious input can reach.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterator
from typing import TypeGuard, TypeVar

import rfc8785

IDENTITY_VERSION = "spec1"

_MAX_SAFE_INTEGER = 2**53 - 1

# The published structural and schema input limits. Structural limits
# bound the raw definition; schema limits bound the identity core's own
# collections. Every one of these raises
# `input_limit_exceeded` -- one shared category for any limit, structural or
# schema, per the vector suite's failure-category vocabulary.
_MAX_DEFINITION_BYTES = 65_536
_MAX_NESTING_DEPTH = 5
_MAX_STRING_LENGTH = 1_024
_MAX_NUMBER_LENGTH = 16
_MAX_SLOTS = 64
_MAX_ENTITY_ROLES = 16
_MAX_VALUES_PER_ROLE = 1_024
_MAX_SET_MEMBERS = 1_024
_MAX_FACET_AXES = 3

# Bound in `_require_type`/`_require_member` so a validated `object` narrows
# to the caller-requested type instead of staying `object` afterward.
_T = TypeVar("_T")

_TOP_LEVEL_KEYS = frozenset(
    {
        "identity_version",
        "shape",
        "slots",
        "entities",
        "open_roles",
        "facets",
        "requested_window",
    }
)
_SLOT_KEYS = frozenset({"property", "binding"})
_SLOT_BINDING_KEYS = frozenset({"state", "ref", "refs"})
_FACET_AXES = frozenset({"provenance", "measurementMethod", "unit"})
_FACET_BINDING_KEYS = frozenset({"state", "ref", "refs", "request"})
# Axes whose `value` binding may carry `"ref": null`: the ask for facets that
# declare no value on the axis. Data Commons omits a facet's
# measurementMethod or unit when it declares none, while every facet carries
# a provenance, so a null provenance ref names nothing and refuses.
_NULLABLE_REF_FACET_AXES = frozenset({"measurementMethod", "unit"})
_UNIT_REQUEST_KEYS = frozenset({"currency", "prices", "base_year"})
_PRICES_VALUES = frozenset({"current", "constant"})
_CURRENCY_ROLE_KEYS = frozenset({"role"})
# The spec's own entity role keys a currency role may name -- deliberately
# not the shape-declared role vocabulary (which is open, see
# `_build_entities`): a currency role is legal only if it is also declared
# by the spec's shape, but that check is a consumer's concern against the
# shape, not this module's.
_CURRENCY_ROLES = frozenset({"observationAbout", "origin", "destination"})
# The two flow roles `open_roles` may name, whose members a source can publish
# beside a total. The set is closed, and the `entities` keys are open.
_OPEN_ROLES = frozenset({"origin", "destination"})
_WINDOW_SIMPLE_KEYS = frozenset({"kind"})
_WINDOW_RANGE_KEYS = frozenset({"kind", "start_year", "end_year"})
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SHAPE_STATVAR_SCHEMA_KEYS = frozenset(
    {
        "kind",
        "population_type",
        "measured_property",
        "stat_type",
        "measurement_qualifier",
        "measurement_denominator",
    }
)
_SHAPE_STATVAR_SCHEMA_STRING_MEMBERS = (
    "population_type",
    "measured_property",
    "stat_type",
    "measurement_qualifier",
)
_SHAPE_STAT_VAR_KEYS = frozenset({"kind", "id"})
_SHAPE_DENOMINATOR_KEYS = frozenset({"ref", "unresolved"})
_DCID_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf"})


class IdentityError(ValueError):
    """A refusal at some stage of the identity pipeline.

    ``category`` is one of the 13 frozen failure-category spellings the
    identity vector suite's README defines (`malformed_json`,
    `duplicate_keys`, `lone_surrogate`, `non_finite_number`,
    `number_out_of_range`, `input_limit_exceeded`, `schema_violation`,
    `unknown_member`, `invalid_binding_shape`, `unsupported_identity_version`,
    `empty_collection`, `reversed_range`, `invalid_unit_request`). Categories
    are permanent once published; a changed meaning gets a new spelling, never
    a redefinition under the same one.
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


# ---------------------------------------------------------------------------
# Stage 1: strict I-JSON parse (bytes only)
# ---------------------------------------------------------------------------


def _object_pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a dict from an object's key/value pairs, rejecting duplicate keys.

    Runs for every JSON object in the document, at every nesting level --
    this is what makes duplicate-key rejection uniform regardless of where
    the repeated key appears.
    """
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise IdentityError("duplicate_keys", f"duplicate object key: {key!r}")
        seen.add(key)
    return dict(pairs)


def _parse_constant(token: str) -> float:
    """Called for the literal tokens NaN/Infinity/-Infinity, which Python's
    json module otherwise accepts silently. I-JSON requires plain finite
    numbers only."""
    raise IdentityError("non_finite_number", f"non-finite number literal: {token}")


def _parse_int(token: str) -> int:
    """Reject an integer literal spelled longer than the maximum number
    length (checked first -- length needs no value interpretation), then
    reject one outside I-JSON's safely representable range (|n| > 2**53 - 1),
    distinct from a value that is non-finite by construction."""
    if len(token) > _MAX_NUMBER_LENGTH:
        raise IdentityError(
            "input_limit_exceeded",
            f"integer literal is {len(token)} characters, exceeding the "
            f"maximum of {_MAX_NUMBER_LENGTH}: {token}",
        )
    value = int(token)
    if abs(value) > _MAX_SAFE_INTEGER:
        raise IdentityError(
            "number_out_of_range",
            f"integer literal out of representable range: {token}",
        )
    return value


def _parse_float(token: str) -> float:
    """Reject a literal spelled longer than the maximum number length first
    (length needs no value interpretation), then decimal/exponent-form
    literals that overflow to +/-inf on conversion (e.g. `1e400`) -- a
    magnitude failure distinct from a literal NaN/Infinity token, which never
    reaches this hook. Also reject a finite but out-of-safe-range magnitude
    (e.g. `1e16`): identity-core numbers are years, so bounding all numbers
    at the I-JSON safe-integer range is the right rule here, matching
    `_parse_int`'s bound."""
    if len(token) > _MAX_NUMBER_LENGTH:
        raise IdentityError(
            "input_limit_exceeded",
            f"number literal is {len(token)} characters, exceeding the "
            f"maximum of {_MAX_NUMBER_LENGTH}: {token}",
        )
    value = float(token)
    if not math.isfinite(value) or abs(value) > _MAX_SAFE_INTEGER:
        raise IdentityError(
            "number_out_of_range", f"number literal out of representable range: {token}"
        )
    return value


def _walk_strings(value: object) -> Iterator[str]:
    """Iterate over every string reachable within `value`, including object
    keys as well as values. An explicit-stack walk, not recursive, so it can
    traverse an arbitrarily deep already-parsed value without exhausting the
    interpreter's recursion limit -- a value entering on the value path
    never passed through `json.loads`, so nothing upstream bounds its depth.

    Shared by `_reject_lone_surrogates` and `_check_string_lengths`, which
    walk identical shape and differ only in what they check about each
    string.
    """
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, dict):
            for key, member in current.items():
                stack.append(key)
                stack.append(member)
        elif isinstance(current, list):
            stack.extend(current)


def _reject_lone_surrogates(value: object) -> None:
    """Walk every string in a parsed JSON value, including object keys, and
    reject any containing an unpaired UTF-16 surrogate code point.

    Python's json decoder joins a valid `\\uD800\\uDC00`-style escape pair into
    one astral character during parsing, so any surrogate code point
    surviving to this point is by construction unpaired -- a plain string
    walk is enough, no explicit pairing logic is needed here.
    """
    for string in _walk_strings(value):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in string):
            raise IdentityError(
                "lone_surrogate", "string contains an unpaired UTF-16 surrogate"
            )


def _check_definition_size(data: bytes) -> None:
    """Reject a definition longer than the maximum definition size (65,536
    bytes), measured on the raw input bytes at entry. Bytes-path only -- a
    caller handing over already-parsed JSON has no bytes left to measure."""
    if len(data) > _MAX_DEFINITION_BYTES:
        raise IdentityError(
            "input_limit_exceeded",
            f"definition is {len(data)} bytes, exceeding the maximum of {_MAX_DEFINITION_BYTES}",
        )


def _check_nesting_depth_bytes(data: bytes) -> None:
    """Pre-scan raw definition bytes for nesting depth before `json.loads`
    runs, so a maliciously deep document refuses with `input_limit_exceeded`
    instead of crashing `json.loads` itself: Python's json decoder recurses
    one call per nesting level and raises an uncaught `RecursionError` around
    1,000 levels deep, a depth a definition far under the 65,536-byte size
    cap can already reach. Bytes-path only, mirroring `_check_definition_size`
    -- runs on the raw bytes ahead of decode, for the same reason: it exists
    to guard `json.loads` itself, so it has to run before it.

    A single linear pass over the raw bytes, counting open containers via
    `{`/`[` and closes via `}`/`]`, with the root document at depth 1 --
    matching `_check_nesting_depth`'s counting convention, and short-
    circuiting the moment depth exceeds the maximum. String-aware: a bracket
    inside a JSON string does not open or close a container, so the scan
    tracks in-string state (toggled on an unescaped `"`) and backslash
    escapes as it goes. It runs directly on the raw bytes with no UTF-8
    decode needed first -- the ASCII bytes it looks for (`"`, `\\`, `{`, `}`,
    `[`, `]`) can never appear as part of a multi-byte UTF-8 sequence, since
    every continuation and lead byte for a non-ASCII code point is >= 0x80.

    Malformed input (mismatched brackets, an unterminated string) may pass
    through this scan uncaught -- it only bounds depth, not JSON validity;
    `json.loads` still refuses malformed input right after, as
    `malformed_json`.
    """
    quote, backslash = ord('"'), ord("\\")
    open_brackets = (ord("{"), ord("["))
    close_brackets = (ord("}"), ord("]"))

    depth = 0
    in_string = False
    escaped = False
    for byte in data:
        if in_string:
            if escaped:
                escaped = False
            elif byte == backslash:
                escaped = True
            elif byte == quote:
                in_string = False
            continue
        if byte == quote:
            in_string = True
        elif byte in open_brackets:
            depth += 1
            if depth > _MAX_NESTING_DEPTH:
                raise IdentityError(
                    "input_limit_exceeded",
                    f"nesting depth exceeds the maximum of {_MAX_NESTING_DEPTH}",
                )
        elif byte in close_brackets:
            depth -= 1


def _check_nesting_depth(value: object) -> None:
    """Reject a value nested past the maximum nesting depth (5), counting
    open containers (objects and arrays) with the root document at depth 1.

    An explicit-stack walk, not recursive, so it can check an arbitrarily
    deep already-parsed value without exhausting the interpreter's recursion
    limit -- unlike the bytes path, a value handed in directly has no
    upstream byte-level pre-scan bounding its depth, so this is the only
    guard for that entry point. Short-circuits with the refusal the moment
    any element's depth exceeds the maximum, rather than computing the
    structure's true maximum depth first.
    """
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > _MAX_NESTING_DEPTH:
            raise IdentityError(
                "input_limit_exceeded",
                f"nesting depth {depth} exceeds the maximum of {_MAX_NESTING_DEPTH}",
            )
        if isinstance(current, dict):
            members = current.values()
        elif isinstance(current, list):
            members = current
        else:
            continue
        for member in members:
            if isinstance(member, (dict, list)):
                stack.append((member, depth + 1))


def _check_string_lengths(value: object) -> None:
    """Walk every string in a parsed JSON value, including object keys, and
    reject any longer than the maximum string length (1,024 Unicode code
    points, counted after JSON escape decoding and before NFC
    normalisation). Shares `_reject_lone_surrogates`'s `_walk_strings` walk.
    """
    for string in _walk_strings(value):
        if len(string) > _MAX_STRING_LENGTH:
            raise IdentityError(
                "input_limit_exceeded",
                f"string of {len(string)} code points exceeds the maximum of {_MAX_STRING_LENGTH}",
            )


def parse_identity_bytes(data: bytes) -> object:
    """Strict I-JSON parse stage only: UTF-8 decode, then JSON parse rejecting
    duplicate keys, NaN/Infinity, out-of-range numbers, lone surrogates, and
    the structural input limits (max definition bytes, nesting depth, string
    length, number length -- the last enforced token-level, inside the
    `parse_int`/`parse_float` hooks below).

    Does not run the identity_version gate or schema validation -- callers
    that already hold a parsed JSON value (e.g. from a wire body a framework
    already decoded) start at `validate_identity_core` or `build_projection`
    instead. Max definition bytes and max number length are checked only
    here: a definition's byte size and a number literal's exact spelling do
    not survive parsing, so a caller entering at `validate_identity_core`/
    `build_projection` has nothing left to check them against. Nesting depth
    and string length are properties of the parsed value itself, so `_build`
    re-checks both for that entry point too.

    Nesting depth is additionally pre-scanned on the raw bytes, ahead of
    `json.loads` (`_check_nesting_depth_bytes`, right after the definition-
    size check below): `json.loads` itself recurses per nesting level and
    raises an uncaught `RecursionError`, not an `IdentityError`, around
    1,000 levels deep, well under the byte-size cap -- the pre-scan exists
    to keep that crash from ever reaching `json.loads` in the first place.
    """
    _check_definition_size(data)
    _check_nesting_depth_bytes(data)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IdentityError(
            "malformed_json", f"input is not valid UTF-8: {exc}"
        ) from exc

    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs_hook,
            parse_constant=_parse_constant,
            parse_int=_parse_int,
            parse_float=_parse_float,
        )
    except json.JSONDecodeError as exc:
        raise IdentityError("malformed_json", f"invalid JSON syntax: {exc}") from exc

    _check_nesting_depth(value)
    _reject_lone_surrogates(value)
    _check_string_lengths(value)
    return value


# ---------------------------------------------------------------------------
# Stage 2 + 3: identity_version gate, schema validation, normalisation
# ---------------------------------------------------------------------------
#
# The identity_version gate runs before any schema-shape check -- `_build`
# below calls `_check_identity_version_gate` right after the strict-parse-
# stage-equivalent checks (nesting depth, lone surrogates, string length; see
# the comment on `_build` itself), so an unsupported version is refused ahead
# of every schema check on both entry points. (The vector suite's vocabulary
# files `unsupported_identity_version` under its strict-parse stage; that
# ordering promise is what the gate-first call here discharges.)
#
# One internal builder (`_build`) does validation and normalisation together
# in a single tree walk: it is the one place that knows the identity core's
# shape, so `validate_identity_core` and `build_projection` can't drift apart
# by construction. `validate_identity_core` just discards the built value.


def _is_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


def _require_object(value: object, description: str) -> dict[str, object]:
    if not _is_object(value):
        raise IdentityError("schema_violation", f"{description} must be a JSON object")
    return value


def _is_number(value: object) -> TypeGuard[int | float]:
    # bool is a subclass of int in Python; JSON true/false must never satisfy
    # a numeric check.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_type(value: object, expected_type: type[_T], description: str) -> _T:
    wrong_type = not isinstance(value, expected_type) or (
        isinstance(value, bool) and expected_type is not bool
    )
    if wrong_type:
        raise IdentityError(
            "schema_violation",
            f"{description} must be of type {expected_type.__name__}",
        )
    return value


def _require_member(obj: dict[str, object], key: str, expected_type: type[_T]) -> _T:
    if key not in obj:
        raise IdentityError("schema_violation", f"missing required member {key!r}")
    return _require_type(obj[key], expected_type, f"member {key!r}")


def _reject_unknown_members(
    obj: dict[str, object], allowed: frozenset[str], description: str
) -> None:
    extra = set(obj) - allowed
    if extra:
        raise IdentityError(
            "unknown_member",
            f"{description} carries unknown member(s): {sorted(extra)!r}",
        )


def _require_integral_number(value: object, description: str) -> int:
    if not _is_number(value):
        raise IdentityError(
            "schema_violation", f"{description} must be an integral JSON number"
        )
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise IdentityError(
                "schema_violation", f"{description} must be an integral JSON number"
            )
        value = int(value)
    # Covers the parsed-value path (spec_id_from_value / build_projection
    # never run the strict-parse hooks, so a float-spelled out-of-range
    # integer -- or a plain Python int already out of range -- must be
    # caught here independently of _parse_int/_parse_float).
    if abs(value) > _MAX_SAFE_INTEGER:
        raise IdentityError(
            "number_out_of_range", f"{description} is out of representable range"
        )
    return value


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _sorted_unique(refs: list[str]) -> list[str]:
    """De-duplicate and sort by Unicode code point order of the NFC form.

    Plain Python `sorted()` on `str` already compares by code point, so no
    casefold/locale collation is involved.
    """
    return sorted({_nfc(ref) for ref in refs})


def _check_identity_version_gate(value: object) -> None:
    """`identity_version` present as a string but not `"spec1"` refuses here,
    before general schema validation runs (the version gate precedes the
    schema stage). A missing or wrongly-typed `identity_version` is left for
    the general required-member check below, which raises `schema_violation`
    for both -- this function only handles the "present, string, wrong value"
    case."""
    if _is_object(value) and "identity_version" in value:
        version = value["identity_version"]
        if isinstance(version, str) and version != IDENTITY_VERSION:
            raise IdentityError(
                "unsupported_identity_version",
                f"unsupported identity_version: {version!r}",
            )


def _build_slot_binding(binding: object) -> dict:
    binding = _require_object(binding, "slot binding")
    _reject_unknown_members(binding, _SLOT_BINDING_KEYS, "slot binding")
    state = _require_member(binding, "state", str)

    if state == "value":
        if "refs" in binding:
            raise IdentityError(
                "invalid_binding_shape", "value-state slot binding cannot carry 'refs'"
            )
        ref = _require_member(binding, "ref", str)
        return {
            "state": "value",
            "ref": _check_dcid_grammar(ref, "slot binding value ref"),
        }

    if state == "set":
        if "ref" in binding:
            raise IdentityError(
                "invalid_binding_shape", "set-state slot binding cannot carry 'ref'"
            )
        refs = _require_member(binding, "refs", list)
        if len(refs) > _MAX_SET_MEMBERS:
            raise IdentityError(
                "input_limit_exceeded",
                f"slot set binding has {len(refs)} members, exceeding the "
                f"maximum of {_MAX_SET_MEMBERS}",
            )
        if not refs:
            raise IdentityError("empty_collection", "slot set binding has zero members")
        checked = []
        for ref in refs:
            _require_type(ref, str, "slot set ref")
            checked.append(_check_dcid_grammar(ref, "slot set ref"))
        normalized = sorted(set(checked))
        if len(normalized) == 1:
            # Singleton normalisation: a set with one member is the same
            # interpretation as a value, so it collapses before hashing.
            return {"state": "value", "ref": normalized[0]}
        return {"state": "set", "refs": normalized}

    if state == "exact":
        # The deliberate parent-only ask: denotes the bound code alone, never
        # its descendants, in contrast to 'value' (see docs/identity-v1.md).
        if "refs" in binding:
            raise IdentityError(
                "invalid_binding_shape", "exact-state slot binding cannot carry 'refs'"
            )
        ref = _require_member(binding, "ref", str)
        return {
            "state": "exact",
            "ref": _check_dcid_grammar(ref, "slot binding exact ref"),
        }

    if state == "unbound":
        if "ref" in binding or "refs" in binding:
            raise IdentityError(
                "invalid_binding_shape",
                "unbound-state slot binding cannot carry 'ref' or 'refs'",
            )
        return {"state": "unbound"}

    raise IdentityError(
        "schema_violation", f"unrecognised slot binding state: {state!r}"
    )


def _build_slot(slot: object) -> dict:
    slot = _require_object(slot, "slot")
    _reject_unknown_members(slot, _SLOT_KEYS, "slot")
    prop = _require_member(slot, "property", str)
    binding = _require_member(slot, "binding", dict)
    return {
        "property": _nfc(prop),
        "binding": _build_slot_binding(binding),
    }


def _build_entities(entities: object) -> dict:
    entities = _require_object(entities, "entities")
    if len(entities) > _MAX_ENTITY_ROLES:
        raise IdentityError(
            "input_limit_exceeded",
            f"entities has {len(entities)} roles, exceeding the maximum of {_MAX_ENTITY_ROLES}",
        )
    result: dict[str, list[str]] = {}
    for role_key, dcids in entities.items():
        dcids = _require_type(dcids, list, f"entities[{role_key!r}]")
        # Counted on the raw array as written, before de-duplication --
        # validation precedes normalisation, so a role with more raw values
        # than the limit is refused even if de-duplication would have
        # brought its distinct count back in bounds.
        if len(dcids) > _MAX_VALUES_PER_ROLE:
            raise IdentityError(
                "input_limit_exceeded",
                f"entity role {role_key!r} has {len(dcids)} members, exceeding "
                f"the maximum of {_MAX_VALUES_PER_ROLE}",
            )
        if not dcids:
            raise IdentityError(
                "empty_collection", f"entity role {role_key!r} has zero members"
            )
        for dcid in dcids:
            _require_type(dcid, str, f"entities[{role_key!r}] member")
        # Byte-identical duplicate keys are already caught pre-NFC at the
        # strict-parse stage (_object_pairs_hook); this catches two
        # byte-distinct raw keys that NFC-normalise to the same key. Both
        # refuse as `duplicate_keys`, never merged: a repeated key after
        # normalisation is refused, not merged.
        normalized_key = _nfc(role_key)
        if normalized_key in result:
            raise IdentityError(
                "duplicate_keys",
                f"entity role key {role_key!r} collides with an existing role "
                f"after NFC normalisation: {normalized_key!r}",
            )
        # Entities never singleton-collapse (contrast with slot/facet sets):
        # they are always arrays, whatever their member count.
        result[normalized_key] = _sorted_unique(dcids)
    return result


def _build_open_roles(open_roles: object, entities: dict[str, list[str]]) -> list[str]:
    """Build `open_roles`, sorted and de-duplicated: the roles the request asks
    for every member of. A role absent from `entities` asks for the total
    instead. A role both open and bound in `entities` refuses, since the two
    asks contradict each other."""
    open_roles = _require_type(open_roles, list, "open_roles")
    for role in open_roles:
        role = _require_type(role, str, "open_roles member")
        if role not in _OPEN_ROLES:
            raise IdentityError(
                "schema_violation",
                f"open_roles member must be one of {sorted(_OPEN_ROLES)!r}: {role!r}",
            )
        if role in entities:
            raise IdentityError(
                "schema_violation",
                f"role {role!r} is both open and bound in entities",
            )
    return sorted(set(open_roles))


def _build_currency(currency: object) -> str | dict:
    """Build the unit request's `currency` member: either an upper-case
    ISO 4217 string (unchanged), or a role object naming one of the spec's
    entity role keys (`observationAbout`, `origin`, `destination`) --
    meaning the currency issued by the entity bound to that role, resolved
    per row. Whether the named role is one the spec's shape actually
    declares is a consumer's concern, checked against the shape rather than
    here.
    """
    if isinstance(currency, str):
        if not _CURRENCY_RE.fullmatch(currency):
            raise IdentityError(
                "invalid_unit_request",
                f"currency must be upper-case ISO 4217: {currency!r}",
            )
        return _nfc(currency)
    if _is_object(currency):
        _reject_unknown_members(
            currency, _CURRENCY_ROLE_KEYS, "unit request currency role"
        )
        role = _require_member(currency, "role", str)
        if role not in _CURRENCY_ROLES:
            raise IdentityError(
                "schema_violation",
                f"currency role must be one of {sorted(_CURRENCY_ROLES)!r}: {role!r}",
            )
        return {"role": _nfc(role)}
    raise IdentityError(
        "schema_violation",
        f"member 'currency' must be a string or object, got {type(currency).__name__}",
    )


def _build_unit_request(request: object) -> dict:
    request = _require_object(request, "unit request")
    _reject_unknown_members(request, _UNIT_REQUEST_KEYS, "unit request")
    if "currency" not in request:
        raise IdentityError("schema_violation", "missing required member 'currency'")
    currency = _build_currency(request["currency"])
    prices = _require_member(request, "prices", str)

    if prices not in _PRICES_VALUES:
        raise IdentityError(
            "schema_violation", f"invalid 'prices' enum value: {prices!r}"
        )

    has_base_year = "base_year" in request
    base_year = (
        _require_integral_number(request["base_year"], "unit request base_year")
        if has_base_year
        else None
    )
    if prices == "constant" and not has_base_year:
        raise IdentityError(
            "invalid_unit_request", "prices='constant' requires 'base_year'"
        )
    if prices == "current" and has_base_year:
        raise IdentityError(
            "invalid_unit_request", "'base_year' is only valid when prices='constant'"
        )

    result = {"currency": currency, "prices": prices}
    if has_base_year:
        result["base_year"] = base_year
    return result


def _build_facet_binding(axis: str, binding: object) -> dict:
    binding = _require_object(binding, f"{axis} facet binding")
    _reject_unknown_members(binding, _FACET_BINDING_KEYS, f"{axis} facet binding")
    state = _require_member(binding, "state", str)
    has_ref = "ref" in binding
    has_refs = "refs" in binding
    has_request = "request" in binding

    # 'request' is part of the union's known vocabulary (the unit axis's
    # conversion-request form), so an unqualified request on any other axis
    # is a wrong-shape-for-declared-state defect, not an unknown member.
    if has_request and axis != "unit":
        raise IdentityError(
            "invalid_binding_shape",
            f"'request' is only valid on the unit axis (got {axis!r})",
        )

    if state == "value":
        if has_refs:
            raise IdentityError(
                "invalid_binding_shape", "value-state facet binding cannot carry 'refs'"
            )
        if has_ref and has_request:
            raise IdentityError(
                "invalid_binding_shape",
                "value-state facet binding cannot carry both 'ref' and 'request'",
            )
        if has_ref:
            if binding["ref"] is None and axis in _NULLABLE_REF_FACET_AXES:
                return {"state": "value", "ref": None}
            ref = _require_type(binding["ref"], str, f"{axis} facet ref")
            return {"state": "value", "ref": _nfc(ref)}
        if has_request:
            request = _require_type(binding["request"], dict, f"{axis} facet request")
            return {"state": "value", "request": _build_unit_request(request)}
        raise IdentityError(
            "schema_violation", "value-state facet binding requires 'ref' or 'request'"
        )

    if state == "set":
        # A set of conversion requests has no use case; conversion requests
        # may only appear in value form.
        if has_request:
            raise IdentityError(
                "invalid_binding_shape",
                "set-state facet binding cannot carry 'request'",
            )
        if has_ref:
            raise IdentityError(
                "invalid_binding_shape", "set-state facet binding cannot carry 'ref'"
            )
        refs = _require_member(binding, "refs", list)
        if len(refs) > _MAX_SET_MEMBERS:
            raise IdentityError(
                "input_limit_exceeded",
                f"{axis} facet set binding has {len(refs)} members, exceeding "
                f"the maximum of {_MAX_SET_MEMBERS}",
            )
        if not refs:
            raise IdentityError(
                "empty_collection", f"{axis} facet set binding has zero members"
            )
        for ref in refs:
            _require_type(ref, str, f"{axis} facet set ref")
        normalized = _sorted_unique(refs)
        if len(normalized) == 1:
            return {"state": "value", "ref": normalized[0]}
        return {"state": "set", "refs": normalized}

    # 'exact' is a slot-binding-only state (see _build_slot_binding and
    # docs/identity-v1.md): a facet has no hierarchical-code denotation to
    # narrow, so it falls through this catch-all like any other unrecognised
    # state rather than gaining its own arm here.
    raise IdentityError(
        "schema_violation", f"unrecognised facet binding state: {state!r}"
    )


def _build_facets(facets: object) -> dict:
    facets = _require_object(facets, "facets")
    # Axis validity is checked before the axis count, the one place this
    # module reverses its usual count-before-per-member order. The axis
    # vocabulary is closed at exactly _MAX_FACET_AXES names, so an
    # over-limit facets object must carry an unknown axis, and refusing
    # that as `unknown_member` is the reading the vector suite's coverage
    # plan states: inventing a fourth axis name "would test the
    # closed-object/unknown-member rule instead of a limit rule", which is
    # why that suite ships its facet-axes over-limit row deferred as
    # inexpressible. Checking the count first would falsify that -- a
    # three-valid-axes-plus-one-unknown object would refuse
    # `input_limit_exceeded`, making the "inexpressible" case reachable
    # under a category no vector pins.
    for axis in facets:
        if axis not in _FACET_AXES:
            raise IdentityError("unknown_member", f"unknown facet axis: {axis!r}")
    # Unreachable while the vocabulary stays closed at three axes; kept as
    # the guard for the day an additive fourth axis makes it expressible.
    if len(facets) > _MAX_FACET_AXES:
        raise IdentityError(
            "input_limit_exceeded",
            f"facets has {len(facets)} axes, exceeding the maximum of {_MAX_FACET_AXES}",
        )
    result: dict[str, dict] = {}
    for axis, binding in facets.items():
        result[axis] = _build_facet_binding(axis, binding)
    return result


def _build_requested_window(window: object) -> dict:
    window = _require_object(window, "requested_window")
    kind = _require_member(window, "kind", str)

    if kind in ("all", "latest"):
        _reject_unknown_members(window, _WINDOW_SIMPLE_KEYS, "requested_window")
        return {"kind": kind}

    if kind == "range":
        _reject_unknown_members(window, _WINDOW_RANGE_KEYS, "requested_window")
        has_start = "start_year" in window
        has_end = "end_year" in window
        if not has_start and not has_end:
            raise IdentityError(
                "schema_violation",
                "range requested_window requires at least one of start_year/end_year",
            )
        start = (
            _require_integral_number(window["start_year"], "start_year")
            if has_start
            else None
        )
        end = (
            _require_integral_number(window["end_year"], "end_year")
            if has_end
            else None
        )
        if start is not None and end is not None and start > end:
            raise IdentityError(
                "reversed_range", f"start_year {start} is greater than end_year {end}"
            )
        result: dict[str, object] = {"kind": "range"}
        if start is not None:
            result["start_year"] = start
        if end is not None:
            result["end_year"] = end
        return result

    raise IdentityError(
        "schema_violation", f"unrecognised requested_window kind: {kind!r}"
    )


def _check_dcid_grammar(value: str, description: str) -> str:
    """NFC-normalise a bare dcid string, then enforce the identity core's
    dcid grammar: non-empty, within the existing string-length cap, no
    Unicode whitespace, no control/format characters (Unicode categories Cc
    or Cf), and no ':' -- identity carries bare dcids, so a `dcid:`-prefixed
    spelling refuses loudly rather than being silently stripped."""
    normalized = _nfc(value)
    if not normalized:
        raise IdentityError("schema_violation", f"{description} must not be empty")
    if len(normalized) > _MAX_STRING_LENGTH:
        raise IdentityError(
            "schema_violation",
            f"{description} exceeds the maximum length of {_MAX_STRING_LENGTH}",
        )
    for ch in normalized:
        if (
            ch == ":"
            or ch.isspace()
            or unicodedata.category(ch) in _DCID_FORBIDDEN_CATEGORIES
        ):
            raise IdentityError(
                "schema_violation", f"{description} is not a valid dcid: {value!r}"
            )
    return normalized


def _build_shape_denominator(denominator: object) -> dict:
    denominator = _require_object(denominator, "shape.measurement_denominator")
    _reject_unknown_members(
        denominator, _SHAPE_DENOMINATOR_KEYS, "shape.measurement_denominator"
    )
    has_ref = "ref" in denominator
    has_unresolved = "unresolved" in denominator

    if has_ref and has_unresolved:
        raise IdentityError(
            "invalid_binding_shape",
            "shape.measurement_denominator cannot carry both 'ref' and 'unresolved'",
        )
    if has_ref:
        ref = _require_member(denominator, "ref", str)
        return {"ref": _check_dcid_grammar(ref, "shape.measurement_denominator.ref")}
    if has_unresolved:
        unresolved = _require_member(denominator, "unresolved", bool)
        if unresolved is not True:
            raise IdentityError(
                "schema_violation",
                "shape.measurement_denominator.unresolved must be the literal true",
            )
        return {"unresolved": True}
    raise IdentityError(
        "schema_violation",
        "shape.measurement_denominator requires exactly one of 'ref' or 'unresolved'",
    )


def _build_shape_statvar_schema(shape: dict[str, object]) -> dict:
    _reject_unknown_members(shape, _SHAPE_STATVAR_SCHEMA_KEYS, "shape")
    result: dict[str, object] = {"kind": "statvar_schema"}
    for member in _SHAPE_STATVAR_SCHEMA_STRING_MEMBERS:
        if member in shape:
            dcid = _require_type(shape[member], str, f"shape.{member}")
            result[member] = _check_dcid_grammar(dcid, f"shape.{member}")
    if "measurement_denominator" in shape:
        denominator = _require_type(
            shape["measurement_denominator"], dict, "shape.measurement_denominator"
        )
        result["measurement_denominator"] = _build_shape_denominator(denominator)
    if len(result) == 1:
        # An arm describing nothing must not mint: at least one of the five
        # schema members has to be present.
        raise IdentityError(
            "schema_violation",
            "shape with kind='statvar_schema' requires at least one schema member",
        )
    return result


def _build_shape_stat_var(shape: dict[str, object]) -> dict:
    _reject_unknown_members(shape, _SHAPE_STAT_VAR_KEYS, "shape")
    dcid = _require_member(shape, "id", str)
    return {"kind": "stat_var", "id": _check_dcid_grammar(dcid, "shape.id")}


def _build_shape(value: object) -> dict:
    """Build the closed two-arm `shape` union: the described ask
    (`kind: "statvar_schema"`, up to five sparse schema members) or the
    pinned ask (`kind: "stat_var"`, a single required dcid `id`). Unknown
    members within either arm refuse, as does an arm carrying a `kind`
    outside this two-member vocabulary."""
    shape = _require_object(value, "shape")
    kind = _require_member(shape, "kind", str)
    if kind == "statvar_schema":
        return _build_shape_statvar_schema(shape)
    if kind == "stat_var":
        return _build_shape_stat_var(shape)
    raise IdentityError("schema_violation", f"unrecognised shape kind: {kind!r}")


def _build(value: object) -> dict:
    # Mirrors parse_identity_bytes's ordering (strict-parse-stage checks --
    # nesting depth, lone surrogates, string length -- before the version
    # gate) so the value-path entry point refuses on the same terms; for the
    # bytes path this makes these walks run twice, which is fine since
    # they're idempotent and cheap. Number length and definition-size are not
    # repeated here -- both are properties of the input's raw bytes or a
    # number literal's exact spelling, neither of which survives parsing, so
    # there is nothing left for the value-path entry point to check.
    #
    # Depth runs first among the walks: unlike the bytes path (guarded ahead
    # of `json.loads` by `_check_nesting_depth_bytes`), a value entering here
    # directly has no upstream bound on its depth, so an over-deep value must
    # refuse on depth before any other walk over the same structure runs.
    _check_nesting_depth(value)
    _reject_lone_surrogates(value)
    _check_string_lengths(value)
    _check_identity_version_gate(value)
    value = _require_object(value, "identity core")
    _reject_unknown_members(value, _TOP_LEVEL_KEYS, "identity core")
    _require_member(value, "identity_version", str)
    raw_shape = _require_member(value, "shape", dict)
    raw_slots = _require_member(value, "slots", list)
    if len(raw_slots) > _MAX_SLOTS:
        raise IdentityError(
            "input_limit_exceeded",
            f"slots array has {len(raw_slots)} entries, exceeding the maximum of {_MAX_SLOTS}",
        )
    raw_entities = _require_member(value, "entities", dict)
    raw_window = _require_member(value, "requested_window", dict)
    if "facets" in value:
        _require_type(value["facets"], dict, "facets")

    shape = _build_shape(raw_shape)
    if shape["kind"] == "stat_var" and raw_slots:
        raise IdentityError(
            "schema_violation",
            "a pinned shape (kind='stat_var') requires an empty slots array",
        )

    slots = [_build_slot(slot) for slot in raw_slots]
    # Sorted by property dcid. The sort is not a total order over duplicate
    # property names, so two slots sharing a property must refuse outright
    # -- retaining both would let input order silently pick which survives
    # the sort, minting different ids for two orderings of the same
    # duplicate-property slot list. The category is `schema_violation`, a
    # cross-member uniqueness constraint on the slots array: `duplicate_keys`
    # is reserved for an object that repeats a key, and slot entries are
    # separate array members.
    seen_properties: set[str] = set()
    for slot in slots:
        prop = slot["property"]
        if prop in seen_properties:
            raise IdentityError(
                "schema_violation",
                f"slot property {prop!r} is named by more than one slot",
            )
        seen_properties.add(prop)
    slots.sort(key=lambda slot: slot["property"])

    entities = _build_entities(raw_entities)
    projection = {
        "identity_version": IDENTITY_VERSION,
        "shape": shape,
        "slots": slots,
        "entities": entities,
        "requested_window": _build_requested_window(raw_window),
    }

    if "open_roles" in value:
        open_roles = _build_open_roles(value["open_roles"], entities)
        if open_roles and shape["kind"] == "stat_var":
            # A pinned StatVar already fixes which roles it carries, so an
            # open role would give one request two spellings.
            raise IdentityError(
                "schema_violation",
                "a pinned shape (kind='stat_var') cannot carry open_roles",
            )
        # Sparse rule, as for facets. An empty list and an omitted member
        # produce identical bytes, so no document without open roles moves.
        if open_roles:
            projection["open_roles"] = open_roles

    if "facets" in value:
        facets = _build_facets(value["facets"])
        # Sparse rule: an explicit `facets: {}` and an omitted member both
        # mean "no axis bound" and must produce identical bytes, so both
        # collapse to leaving the member out entirely.
        if facets:
            projection["facets"] = facets

    return projection


def validate_identity_core(value: object) -> None:
    """Run the identity_version gate and schema validation. Raises
    `IdentityError` on any violation; returns nothing on success."""
    _build(value)


def build_projection(value: object) -> dict:
    """Validate `value`, then normalise and return the identity projection
    (pre-serialisation): NFC-normalised strings, slots sorted by property
    with every property bound at most once, sorted and de-duplicated
    entity/facet-set and `open_roles` arrays, singleton sets collapsed, and an
    empty `facets` or `open_roles` member omitted."""
    return _build(value)


# ---------------------------------------------------------------------------
# Stage 4 + 5: JCS serialisation and spec_id minting
# ---------------------------------------------------------------------------


def canonicalize(projection: dict) -> bytes:
    """Serialise a projection to its canonical JCS (RFC 8785) bytes."""
    return rfc8785.dumps(projection)


def mint_spec_id(canonical: bytes) -> str:
    """`spec_` plus the first 32 lowercase hex characters of SHA-256 of the
    canonical bytes."""
    digest = hashlib.sha256(canonical).hexdigest()
    return f"spec_{digest[:32]}"


def spec_id_from_bytes(data: bytes) -> str:
    """Run the full pipeline from raw identity-core bytes to a minted spec_id."""
    value = parse_identity_bytes(data)
    return spec_id_from_value(value)


def spec_id_from_value(value: object) -> str:
    """Run the full pipeline from an already-parsed identity-core value to a
    minted spec_id."""
    projection = build_projection(value)
    canonical = canonicalize(projection)
    return mint_spec_id(canonical)
