from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from nodepoint.auth.api_scopes import SCOPE_CODES
from nodepoint.enums import UserRole
from typing import List


def default_scopes_for_role(role: str) -> List[str] | None:
    """None means unrestricted (all scopes)."""
    if role in (UserRole.SUPERADMIN, UserRole.ADMIN):
        return None
    return [
        "workspace:read",
        "document:read",
        "chat:read",
        "group:read",
        "kg:read",
        "preprocess:read",
        "preprocess:write",
    ]


def effective_allowed_scopes(user: AbstractBaseUser) -> List[str] | None:
    from nodepoint.auth.users import ensure_profile

    profile = ensure_profile(user)
    if profile.allowed_scopes is not None:
        return list(profile.allowed_scopes)
    return default_scopes_for_role(profile.role)


def user_has_scope(user: AbstractBaseUser, required: str | None) -> bool:
    if required is None:
        return True
    allowed = effective_allowed_scopes(user)
    if allowed is None:
        return True
    return required in allowed


def scopes_assignable_by(actor: AbstractBaseUser) -> List[str] | None:
    """Scopes an admin may grant to managed users (subset of their own)."""
    return effective_allowed_scopes(actor)


def validate_scopes_subset(
    scopes: List[str], *, assigner: AbstractBaseUser
) -> List[str]:
    unknown = [s for s in scopes if s not in SCOPE_CODES]
    if unknown:
        raise ValueError(f"Unknown scopes: {unknown}")
    assignable = scopes_assignable_by(assigner)
    if assignable is None:
        return scopes
    assignable_set = set(assignable)
    excess = [s for s in scopes if s not in assignable_set]
    if excess:
        raise ValueError(f"Cannot assign scopes you do not hold: {excess}")
    return scopes
