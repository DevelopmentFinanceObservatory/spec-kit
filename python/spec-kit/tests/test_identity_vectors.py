"""Conformance harness: runs dfo_spec_kit.identity against the identity
vector suite.

The vector suite (`vectors/identity/*.json`) is the normative,
language-neutral conformance target for the identity projection and
canonicalisation algorithm -- see `schemas/vectors/README.md` for the record
format and the frozen failure-category vocabulary. Where a vector and the
prose disagree, the vector wins until the disagreement is escalated.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from dfo_spec_kit.identity import (
    IdentityError,
    build_projection,
    canonicalize,
    mint_spec_id,
    parse_identity_bytes,
    spec_id_from_value,
)
from dfo_spec_kit.identity.core import _check_dcid_grammar

# Kept in step with the drafted-vector count `vectors/identity/README.md`
# states; bump both together when a vector is added, so the floor still
# catches a single missing vector instead of trailing behind.
_MIN_DRAFTED_VECTORS = 128

# Repo root is three levels up from this file (tests/ -> spec-kit -> python
# -> repo root); the vectors are always present in-repo, so a missing or
# empty vector directory is a hard test failure below, never a skip.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_VECTOR_PATHS = sorted((_REPO_ROOT / "vectors" / "identity").glob("*.json"))


def _load_vector(path: Path) -> dict:
    with path.open("rb") as f:
        return json.load(f)


_VECTORS = [_load_vector(path) for path in _VECTOR_PATHS]


def test_suite_is_non_empty_and_meets_drafted_floor() -> None:
    """A missing or truncated vector suite must fail loudly, never silently
    pass with zero (or too few) vectors exercised."""
    assert len(_VECTORS) > 0, (
        f"no identity vectors found under {_REPO_ROOT / 'vectors' / 'identity'}"
    )
    assert len(_VECTORS) >= _MIN_DRAFTED_VECTORS, (
        f"expected at least {_MIN_DRAFTED_VECTORS} drafted vectors, "
        f"found {len(_VECTORS)} -- checkout may be truncated"
    )


def _build_projection_for(vector: dict) -> dict:
    """Run the vector's input through the pipeline stage its form implies:
    an input_bytes vector starts at parse_identity_bytes (the strict parse
    stage), an input vector is already-parsed JSON and starts at
    build_projection (the schema-validation stage onward)."""
    if "input_bytes" in vector:
        value = parse_identity_bytes(bytes.fromhex(vector["input_bytes"]))
    else:
        value = vector["input"]
    return build_projection(value)


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["name"] for v in _VECTORS])
def test_identity_vector(vector: dict) -> None:
    if vector["classification"] == "negative":
        with pytest.raises(IdentityError) as excinfo:
            _build_projection_for(vector)
        assert excinfo.value.category == vector["failure_category"]
        return

    projection = _build_projection_for(vector)
    assert projection == vector["expected_projection"]

    canonical = canonicalize(projection)
    assert canonical == bytes.fromhex(vector["canonical_bytes"])

    spec_id = mint_spec_id(canonical)
    assert spec_id == vector["spec_id"]


# ---------------------------------------------------------------------------
# Non-vector test: a worked example, not in the vector set, so it has no
# pinned expected id -- only the shape of the output.
# ---------------------------------------------------------------------------


def test_worked_example_round_trips_to_a_valid_spec_id() -> None:
    identity_core = {
        "identity_version": "spec1",
        "shape": {
            "kind": "statvar_schema",
            "measured_property": "developmentFinanceFlow",
        },
        "slots": [
            {
                "property": "DevelopmentFinancePurpose",
                "binding": {"state": "value", "ref": "Malaria"},
            },
            {
                "property": "DevelopmentFinanceScheme",
                "binding": {"state": "value", "ref": "GrantsScheme"},
            },
        ],
        "entities": {
            "origin": ["country/FRA"],
            "destination": ["country/TGO"],
        },
        "facets": {
            "provenance": {"state": "value", "ref": "OECD_DAC2A"},
            "measurementMethod": {"state": "value", "ref": "GrantEquivalent"},
            "unit": {
                "state": "value",
                "request": {"base_year": 2023, "currency": "EUR", "prices": "constant"},
            },
        },
        "requested_window": {"kind": "range", "start_year": 2015, "end_year": 2024},
    }

    spec_id = spec_id_from_value(identity_core)

    assert spec_id.startswith("spec_")
    assert len(spec_id) == len("spec_") + 32
    hex_part = spec_id[len("spec_") :]
    assert len(hex_part) == 32
    int(hex_part, 16)  # must be valid lowercase hex
    assert hex_part == hex_part.lower()


# ---------------------------------------------------------------------------
# Non-vector tests: edge cases not covered by the published vector suite.
# ---------------------------------------------------------------------------


def _minimal_document(**overrides: object) -> dict:
    doc: dict = {
        "identity_version": "spec1",
        "shape": {"kind": "statvar_schema", "measured_property": "test_shape"},
        "slots": [],
        "entities": {},
        "requested_window": {"kind": "all"},
    }
    doc.update(overrides)
    return doc


class TestOutOfRangeYearBeyondSafeInteger:
    """An integral number spelled in float syntax whose magnitude exceeds
    the I-JSON safe-integer range (2**53 - 1) must refuse with
    `number_out_of_range` at both entry paths -- left unchecked, it reaches
    `canonicalize` and crashes with rfc8785's own `IntegerDomainError`, a
    ValueError but NOT an IdentityError, violating the refuse-with-category
    contract."""

    def test_bytes_path_float_spelled_out_of_range_year(self) -> None:
        doc = _minimal_document(requested_window={"kind": "range", "start_year": 1e16})
        data = json.dumps(doc).encode("utf-8")
        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(parse_identity_bytes(data))
        assert excinfo.value.category == "number_out_of_range"

    def test_value_path_plain_int_out_of_range_year(self) -> None:
        doc = _minimal_document(
            requested_window={"kind": "range", "start_year": 10**20}
        )
        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(doc)
        assert excinfo.value.category == "number_out_of_range"

    def test_value_path_float_spelled_out_of_range_year(self) -> None:
        doc = _minimal_document(requested_window={"kind": "range", "start_year": 1e16})
        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(doc)
        assert excinfo.value.category == "number_out_of_range"

    def test_at_limit_year_mints_and_float_and_int_spellings_converge(self) -> None:
        limit = 2**53 - 1
        doc_int = _minimal_document(
            requested_window={"kind": "range", "start_year": limit}
        )
        doc_float = _minimal_document(
            requested_window={"kind": "range", "start_year": float(limit)}
        )
        id_from_int = spec_id_from_value(doc_int)
        id_from_float = spec_id_from_value(doc_float)
        assert id_from_int == id_from_float

        # A float LITERAL (as opposed to a Python float object) spelling this
        # same boundary value on the bytes path is a different case under the
        # published max_number_length limit (16 characters): Python's default
        # JSON writer spells float(2**53 - 1) as "9007199254740991.0", 18
        # characters -- the 16 significant digits plus the ".0" float marker
        # -- which exceeds the limit. This particular value has no shorter
        # float spelling: it isn't a round decimal, so all 16 significant
        # digits are needed, and the ".0" marker always adds at least one
        # character past them. The number-length check is token-level and
        # runs before magnitude, so this refuses as input_limit_exceeded
        # rather than converging with id_from_int.
        data = json.dumps(doc_float).encode("utf-8")
        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(parse_identity_bytes(data))
        assert excinfo.value.category == "input_limit_exceeded"


class TestNFCCollidingEntityRoleKeysRefuse:
    """Two entity role keys that are byte-distinct but NFC-normalise to the
    same key must refuse with `duplicate_keys` -- byte-identical duplicate
    keys are already caught pre-NFC at the strict-parse stage, so this
    covers the post-normalisation collision `_build_entities` checks
    separately."""

    _DECOMPOSED_KEY = "róle"  # "role" with a combining acute accent (NFD-style)
    _PRECOMPOSED_KEY = "róle"  # the same word, NFC-composed

    def test_bytes_path(self) -> None:
        doc = _minimal_document(
            entities={self._DECOMPOSED_KEY: ["a"], self._PRECOMPOSED_KEY: ["b"]}
        )
        data = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(parse_identity_bytes(data))
        assert excinfo.value.category == "duplicate_keys"

    def test_value_path(self) -> None:
        doc = _minimal_document(
            entities={self._DECOMPOSED_KEY: ["a"], self._PRECOMPOSED_KEY: ["b"]}
        )
        with pytest.raises(IdentityError) as excinfo:
            build_projection(doc)
        assert excinfo.value.category == "duplicate_keys"


class TestSchemaGuardsSurviveOptimizedMode:
    """The schema-narrowing helpers (`_require_object`/`_require_type`) must
    execute unconditionally, not only inside an `assert` expression --
    `python -O`/`PYTHONOPTIMIZE` strips `assert` statements including the
    expression they wrap, so a guard reachable only via `assert helper(...)`
    would silently stop running under optimized mode and let a malformed
    value reach later stages as an uncategorised error instead of refusing
    with `IdentityError`."""

    _SCRIPT = (
        "from dfo_spec_kit.identity import IdentityError, build_projection\n"
        "doc = {\n"
        "    'identity_version': 'spec1',\n"
        "    'shape': 5,\n"
        "    'slots': [],\n"
        "    'entities': {},\n"
        "    'requested_window': {'kind': 'all'},\n"
        "}\n"
        "try:\n"
        "    build_projection(doc)\n"
        "except IdentityError as exc:\n"
        "    assert exc.category == 'schema_violation', exc.category\n"
        "    print('IdentityError-raised')\n"
        "else:\n"
        "    raise SystemExit('build_projection did not raise for a non-object shape')\n"
    )

    def test_malformed_shape_still_refuses_under_dash_o(self) -> None:
        result = subprocess.run(
            [sys.executable, "-O", "-c", self._SCRIPT],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "IdentityError-raised" in result.stdout


class TestLoneSurrogateInParsedValueRefuses:
    """A lone (unpaired) UTF-16 surrogate reaching `build_projection` or
    `spec_id_from_value` directly -- i.e. an already-parsed value that never
    passed through `parse_identity_bytes` -- must refuse with
    `lone_surrogate`, same as the bytes path. Previously the surrogate walk
    only ran in `parse_identity_bytes`, so a caller handing in an
    already-parsed value (e.g. a wire body a framework already decoded)
    could mint from a surrogate-bearing string; the offending string only
    broke later, inside rfc8785, with a non-`IdentityError`."""

    _LONE_SURROGATE = "\ud800"

    def test_ref_string(self) -> None:
        doc = _minimal_document(
            slots=[
                {
                    "property": "p",
                    "binding": {"state": "value", "ref": self._LONE_SURROGATE},
                }
            ]
        )
        with pytest.raises(IdentityError) as excinfo:
            build_projection(doc)
        assert excinfo.value.category == "lone_surrogate"

        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(doc)
        assert excinfo.value.category == "lone_surrogate"

    def test_object_key(self) -> None:
        doc = _minimal_document(entities={self._LONE_SURROGATE: ["a"]})
        with pytest.raises(IdentityError) as excinfo:
            build_projection(doc)
        assert excinfo.value.category == "lone_surrogate"

        with pytest.raises(IdentityError) as excinfo:
            spec_id_from_value(doc)
        assert excinfo.value.category == "lone_surrogate"


class TestNestingDepthCheckedOnBothEntryPaths:
    """Nesting depth is a property of the parsed value itself, unlike max
    bytes/max number length (bytes-path-only, since neither a definition's
    raw byte size nor a number literal's exact spelling survives parsing), so
    an over-limit refusal must fire whether the caller enters via
    `parse_identity_bytes` or hands in an already-parsed value directly. The
    vector suite's nesting-depth vectors split the boundary case across entry
    paths (at-limit via a parsed value, over-limit via bytes) rather than
    covering the same input on both; these tests close that gap, mirroring
    `TestNFCCollidingEntityRoleKeysRefuse`'s shape."""

    _OVER_LIMIT_SLOTS: ClassVar = [
        {"property": "p", "binding": {"state": "set", "refs": [["nested"]]}}
    ]

    def test_bytes_path(self) -> None:
        doc = _minimal_document(slots=self._OVER_LIMIT_SLOTS)
        data = json.dumps(doc).encode("utf-8")
        with pytest.raises(IdentityError) as excinfo:
            parse_identity_bytes(data)
        assert excinfo.value.category == "input_limit_exceeded"

    def test_value_path(self) -> None:
        doc = _minimal_document(slots=self._OVER_LIMIT_SLOTS)
        with pytest.raises(IdentityError) as excinfo:
            build_projection(doc)
        assert excinfo.value.category == "input_limit_exceeded"


class TestStringLengthCheckedOnBothEntryPaths:
    """String length is likewise a property of the parsed value itself. The
    vector suite's string-length vectors both use a parsed `input` (value
    path only); these tests close the bytes-path gap the same way
    `TestNestingDepthCheckedOnBothEntryPaths` does for nesting depth."""

    _OVER_LIMIT_MEASURED_PROPERTY = "x" * 1025

    def test_bytes_path(self) -> None:
        doc = _minimal_document(
            shape={
                "kind": "statvar_schema",
                "measured_property": self._OVER_LIMIT_MEASURED_PROPERTY,
            }
        )
        data = json.dumps(doc).encode("utf-8")
        with pytest.raises(IdentityError) as excinfo:
            parse_identity_bytes(data)
        assert excinfo.value.category == "input_limit_exceeded"

    def test_value_path(self) -> None:
        doc = _minimal_document(
            shape={
                "kind": "statvar_schema",
                "measured_property": self._OVER_LIMIT_MEASURED_PROPERTY,
            }
        )
        with pytest.raises(IdentityError) as excinfo:
            build_projection(doc)
        assert excinfo.value.category == "input_limit_exceeded"


class TestNestingDepthGuardIsRecursionSafe:
    """The nesting-depth enforcement must survive the adversarial input it
    exists to stop. `json.loads` itself raises an uncaught `RecursionError`
    -- not an `IdentityError` -- around 1,000 levels of nesting, a depth a
    payload far under the 65,536-byte definition-size cap can already reach.
    The depth-checking walks are iterative (explicit-stack), not recursive,
    so they check an arbitrarily deep value without exhausting the
    interpreter's own stack. These tests go far past that ~1,000-level
    threshold to confirm that holds, not just to skim near it.

    The bytes-path case is capped at a depth that stays under the
    definition-size limit on purpose, so it's the nesting-depth pre-scan
    doing the refusing, not the size check -- two bytes per level of nesting
    means no bytes payload can reach 100,000 levels without also tripping
    the size cap first."""

    def test_deep_bytes_payload_refuses_via_the_prescan(self) -> None:
        depth = 32_000
        data = b"[" * depth + b"1" + b"]" * depth
        assert len(data) < 65_536
        with pytest.raises(IdentityError) as excinfo:
            parse_identity_bytes(data)
        assert excinfo.value.category == "input_limit_exceeded"

    def test_deep_parsed_value_refuses_via_the_value_level_check(self) -> None:
        # No byte-size cap applies on the value path, so this can go far
        # deeper than any bytes payload could -- 100,000 levels, built
        # iteratively (a Python loop, not recursion).
        value: object = 1
        for _ in range(100_000):
            value = [value]
        with pytest.raises(IdentityError) as excinfo:
            build_projection(value)
        assert excinfo.value.category == "input_limit_exceeded"

    def test_bracket_lookalike_in_a_string_does_not_trip_the_prescan(self) -> None:
        doc = _minimal_document(
            slots=[{"property": "p", "binding": {"state": "value", "ref": "[[[[[["}}]
        )
        data = json.dumps(doc).encode("utf-8")
        value = parse_identity_bytes(data)
        spec_id = spec_id_from_value(value)
        assert spec_id.startswith("spec_")

    def test_ordinary_depth_six_input_still_refuses_alongside_a_bracket_lookalike(
        self,
    ) -> None:
        # Regression guard for the prescan's string-awareness: a bracket
        # lookalike sitting next to genuine over-depth nesting must not make
        # the scan under-count the real structural depth.
        doc = _minimal_document(
            slots=[
                {
                    "property": "p",
                    "binding": {"state": "set", "refs": [["nested", "[[[["]]},
                }
            ]
        )
        data = json.dumps(doc).encode("utf-8")
        with pytest.raises(IdentityError) as excinfo:
            parse_identity_bytes(data)
        assert excinfo.value.category == "input_limit_exceeded"


class TestUnitRequestCurrencyRoleShapeEdgeCases:
    """`_build_unit_request`'s `currency` member accepts either an
    upper-case ISO 4217 string or a role object naming one of
    `observationAbout`/`origin`/`destination` -- the vector suite's
    `unit-request-currency-role-*` files cover the unrecognised-role,
    extra-member, and lowercase-role refusals; this covers the one case
    those vectors do not: a `role` member present but not a string."""

    def _doc_with_currency(self, currency: object) -> dict:
        return _minimal_document(
            facets={
                "unit": {
                    "state": "value",
                    "request": {"currency": currency, "prices": "current"},
                }
            }
        )

    def test_refuses_non_string_role(self) -> None:
        with pytest.raises(IdentityError) as excinfo:
            build_projection(self._doc_with_currency({"role": 1}))
        assert excinfo.value.category == "schema_violation"

    def test_refuses_currency_that_is_neither_string_nor_object(self) -> None:
        with pytest.raises(IdentityError) as excinfo:
            build_projection(self._doc_with_currency(1))
        assert excinfo.value.category == "schema_violation"

    def test_refuses_currency_as_a_list(self) -> None:
        # A list is neither a string nor an object -- the same
        # neither-shape refusal a bare number gets above.
        with pytest.raises(IdentityError) as excinfo:
            build_projection(self._doc_with_currency(["USD"]))
        assert excinfo.value.category == "schema_violation"


class TestDcidGrammarEnforcesItsOwnStringLengthCap:
    """`_check_dcid_grammar` enforces the maximum string length on the value
    it is checking by itself, independently of `_check_string_lengths`'s own
    walk over the whole parsed document: called directly with a string past
    the cap, it refuses with `schema_violation`, the dcid grammar's own
    category for a malformed dcid."""

    _OVER_LENGTH_VALUE = "x" * 1025

    def test_over_length_value_refuses_with_schema_violation(self) -> None:
        with pytest.raises(IdentityError) as excinfo:
            _check_dcid_grammar(self._OVER_LENGTH_VALUE, "shape.measured_property")
        assert excinfo.value.category == "schema_violation"
