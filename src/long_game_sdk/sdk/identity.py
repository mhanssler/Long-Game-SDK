"""Shared strict identity parsing and field-specific comparison semantics."""

from __future__ import annotations

import unicodedata

IdentityFields = tuple[str, str, str]
_IDENTITY_FIELD_NAMES = ("manufacturer", "model", "serial")


def normalize_identity_value(value: str) -> str:
    """Reject controls/non-ASCII whitespace, then trim ordinary ASCII spaces."""
    if any(
        unicodedata.category(character).startswith("C")
        or (character.isspace() and character != " ")
        for character in value
    ):
        raise ValueError("control characters are not allowed in identity values")
    return value.strip(" ")


def parse_identity(value: str) -> IdentityFields | None:
    """Parse the first three IDN fields after strict whole-value validation."""
    try:
        normalized = normalize_identity_value(value)
        parts = tuple(normalize_identity_value(part) for part in normalized.split(","))
    except ValueError:
        return None
    if len(parts) < 3 or any(not part for part in parts[:3]):
        return None
    return parts[0], parts[1], parts[2]


def parse_identity_response(value: str) -> IdentityFields | None:
    """Remove one SCPI line terminator, then apply strict identity validation.

    VISA backends may return the protocol framing terminator as part of ``query``.
    Only one terminal LF or CRLF is framing; embedded, repeated, bare-CR, NUL,
    and all other control characters remain invalid.
    """

    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    return parse_identity(value)


def identity_field_equal(field: str, left: str, right: str) -> bool:
    """Compare vendor/model case-insensitively and serial case-sensitively."""
    left_normalized = normalize_identity_value(left)
    right_normalized = normalize_identity_value(right)
    if field == "serial":
        return left_normalized == right_normalized
    return left_normalized.casefold() == right_normalized.casefold()


def identities_equal(left: IdentityFields, right: IdentityFields) -> bool:
    """Compare complete identities with field-specific semantics."""
    return all(
        identity_field_equal(field, left_value, right_value)
        for field, left_value, right_value in zip(_IDENTITY_FIELD_NAMES, left, right)
    )
