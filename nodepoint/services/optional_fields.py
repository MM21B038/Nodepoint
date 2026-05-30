from __future__ import annotations

TAG_MAX_LENGTH = 255
DESCRIPTION_MAX_LENGTH = 4096


def normalize_optional_field(
    value: str | None, *, field_name: str, max_length: int
) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip()
    if len(cleaned) > max_length:
        raise ValueError(
            f"{field_name} must be at most {max_length} characters"
        )
    return cleaned


def normalize_tag(tag: str | None) -> str:
    return normalize_optional_field(
        tag, field_name="Tag", max_length=TAG_MAX_LENGTH
    )


def normalize_description(description: str | None) -> str:
    return normalize_optional_field(
        description,
        field_name="Description",
        max_length=DESCRIPTION_MAX_LENGTH,
    )


def optional_field_for_api(value: str) -> str | None:
    return value or None
