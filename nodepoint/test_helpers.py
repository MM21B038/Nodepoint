"""Helpers for tests after auth/ownership was introduced."""

from __future__ import annotations

import uuid

from nodepoint.auth.users import User, create_account
from nodepoint.enums import UserRole
from nodepoint.models import Workspace


def create_test_user(
    *,
    username: str | None = None,
    password: str = "TestPass123!",
    role: str = UserRole.USER,
    managed_by=None,
    allowed_scopes=None,
):
    if username is None:
        username = f"user-{uuid.uuid4().hex[:8]}"
    return create_account(
        username=username,
        password=password,
        role=role,
        managed_by=managed_by,
        allowed_scopes=allowed_scopes,
    )


def authenticated_client(user=None):
    from rest_framework.test import APIClient

    user = user or create_test_user()
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def create_test_workspace(
    *,
    name: str | None = None,
    owner=None,
    **kwargs,
) -> Workspace:
    if name is None:
        name = f"ws-{uuid.uuid4().hex[:8]}"
    if owner is None:
        owner = create_test_user()
    return Workspace.objects.create(name=name, owner=owner, **kwargs)
