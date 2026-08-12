"""Shared strict identity parsing and field-specific comparison semantics."""

from __future__ import annotations

IdentityFields = tuple[str, str, str]
_IDENTITY_FIELD_NAMES = ("manufacturer", "model", "serial")


def normalize_identity_value(value: str) -> str:
    """Reject C0/DEL controls before trimming ordinary surrounding spaces."""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("control characters are not allowed in identity values")
    return value.strip()


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
