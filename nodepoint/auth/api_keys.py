"""API key generation and validation."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from nodepoint.auth.api_scopes import SCOPE_CODES
from django.contrib.auth import get_user_model

from nodepoint.models import ApiKey

User = get_user_model()

EXPIRY_PRESETS: dict[str, timedelta] = {
    "3_months": timedelta(days=90),
    "6_months": timedelta(days=180),
    "12_months": timedelta(days=365),
    "2_years": timedelta(days=730),
}


class ApiKeyError(ValueError):
    pass


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key_pair() -> tuple[str, str, str]:
    """Return (full_key, prefix, key_hash)."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full_key = f"np_{prefix}_{secret}"
    return full_key, prefix, _hash_key(full_key)


def expires_at_from_preset(preset: str) -> timezone.datetime:
    delta = EXPIRY_PRESETS.get(preset)
    if delta is None:
        raise ApiKeyError(
            f"Invalid expiry_preset. Choose one of: {', '.join(EXPIRY_PRESETS)}"
        )
    return timezone.now() + delta


def validate_scopes(scopes: list[str]) -> list[str]:
    cleaned = []
    for scope in scopes:
        if scope not in SCOPE_CODES:
            raise ApiKeyError(f"Unknown scope: {scope}")
        cleaned.append(scope)
    if not cleaned:
        raise ApiKeyError("At least one scope is required")
    return cleaned


def create_api_key(
    *,
    user: User,
    created_by: User,
    name: str,
    scopes: list[str],
    expiry_preset: str,
) -> tuple[ApiKey, str]:
    allowed = validate_scopes(scopes)
    full_key, prefix, key_hash = generate_api_key_pair()
    api_key = ApiKey.objects.create(
        user=user,
        created_by=created_by,
        name=(name or "").strip(),
        prefix=prefix,
        key_hash=key_hash,
        allowed_scopes=allowed,
        expires_at=expires_at_from_preset(expiry_preset),
    )
    return api_key, full_key


def lookup_api_key(raw_key: str) -> ApiKey | None:
    if not raw_key.startswith("np_"):
        return None
    parts = raw_key.split("_", 2)
    if len(parts) < 3:
        return None
    prefix = parts[1]
    try:
        api_key = ApiKey.objects.select_related("user").get(
            prefix=prefix, is_active=True
        )
    except ApiKey.DoesNotExist:
        return None
    if _hash_key(raw_key) != api_key.key_hash:
        return None
    if api_key.expires_at <= timezone.now():
        return None
    return api_key
