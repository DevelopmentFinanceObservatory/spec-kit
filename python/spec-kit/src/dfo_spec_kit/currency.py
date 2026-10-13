"""The currency-code shape test the identity, the frame grammar and the unit
reader share.

This module reads no data file, so importing it cannot fail on a registry.
"""

from __future__ import annotations


def is_currency_code(value: object) -> bool:
    """Whether `value` is a currency code: three upper-case ASCII letters.

    The test is by shape, so a code the conversion service cannot target
    (`ZWG`) or a withdrawn code (`DEM`) still types a unit. Which currencies
    a conversion can reach is the converter's own list.
    """
    return (
        isinstance(value, str)
        and len(value) == 3
        and value.isascii()
        and value.isalpha()
        and value.isupper()
    )


__all__ = ["is_currency_code"]
