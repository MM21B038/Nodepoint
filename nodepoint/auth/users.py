from __future__ import annotations

from typing import List, TypeGuard

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, User
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request

from nodepoint.auth.scopes import default_scopes_for_role
from nodepoint.enums import AccountStatus, UserRole
from nodepoint.models import UserProfile

# Concrete default AUTH_USER_MODEL. Import the class (not get_user_model()) so
# `User` is valid in type annotations under Pyright/basedpyright.


def is_authenticated_user(user: object) -> TypeGuard[User]:
    """Narrow request.user / scope user away from AnonymousUser."""
    return isinstance(user, User) and bool(user.is_authenticated)


def request_actor(request: Request) -> User:
    """Return the authenticated request user, or raise NotAuthenticated."""
    user = request.user
    if not is_authenticated_user(user):
        raise NotAuthenticated()
    return user


def as_actor(user: AbstractBaseUser | AnonymousUser | User) -> User:
    """Narrow websocket / AbstractBaseUser actors to concrete User."""
    if not is_authenticated_user(user):
        raise NotAuthenticated()
    return user


def ensure_profile(user: AbstractBaseUser) -> UserProfile:
    # Reverse OneToOne lives on the concrete AUTH_USER_MODEL, not AbstractBaseUser.
    # Missing profiles raise RelatedObjectDoesNotExist (AttributeError subclass),
    # so getattr(..., None) is both type-safe and correct at runtime.
    profile = getattr(user, "profile", None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={"role": UserRole.USER},
        )
        setattr(user, "profile", profile)
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
    allowed_scopes: List[str] | None = None,
) -> User:
    user = User.objects.create_user(username=username, password=password)
    if allowed_scopes is None:
        allowed_scopes = default_scopes_for_role(role)
    profile = UserProfile.objects.create(
        user=user,
        role=role,
        managed_by=managed_by,
        created_by=created_by,
        allowed_scopes=allowed_scopes,
        status=AccountStatus.ACTIVE,
    )
    setattr(user, "profile", profile)
    return user
