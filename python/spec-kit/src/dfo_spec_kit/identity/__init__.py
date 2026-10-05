"""Identity: role-slot projection, JCS canonicalisation, SHA-256 spec_id minting.

Implements the spec1 identity scheme.
"""

from dfo_spec_kit.identity.core import (
    IDENTITY_VERSION,
    IdentityError,
    build_projection,
    canonicalize,
    mint_spec_id,
    parse_identity_bytes,
    spec_id_from_bytes,
    spec_id_from_value,
    validate_identity_core,
)

__all__ = [
    "IDENTITY_VERSION",
    "IdentityError",
    "build_projection",
    "canonicalize",
    "mint_spec_id",
    "parse_identity_bytes",
    "spec_id_from_bytes",
    "spec_id_from_value",
    "validate_identity_core",
]
