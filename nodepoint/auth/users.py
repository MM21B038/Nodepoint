from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser

from nodepoint.auth.scopes import default_scopes_for_role
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.models import UserProfile

User = get_user_model()


def ensure_profile(user: AbstractBaseUser) -> UserProfile:
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        profile = None
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={"role": UserRole.USER},
        )
    user.profile = profile
    return profile


def user_role(user: AbstractBaseUser) -> str:
    return ensure_profile(user).role


def is_superadmin(user: AbstractBaseUser) -> bool:
    return user_role(user) == UserRole.SUPERADMIN


def is_admin(user: AbstractBaseUser) -> bool:
    return user_role(user) in (UserRole.SUPERADMIN, UserRole.ADMIN)


def create_account(
    *,
    username: str,
    password: str,
    role: str,
    managed_by: AbstractBaseUser | None = None,
    created_by: AbstractBaseUser | None = None,
    allowed_scopes: list[str] | None = None,
) -> AbstractBaseUser:
    user = User.objects.create_user(username=username, password=password)
    if allowed_scopes is None:
        allowed_scopes = default_scopes_for_role(role)
    UserProfile.objects.create(
        user=user,
        role=role,
        managed_by=managed_by,
        created_by=created_by,
        allowed_scopes=allowed_scopes,
        status=AccountStatus.ACTIVE,
    )
    return user
