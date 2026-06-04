from __future__ import annotations

from rest_framework.permissions import BasePermission

from nodepoint.auth.api_scopes import scope_for_request
from nodepoint.auth.scopes import user_has_scope
from nodepoint.auth.users import user_role
from nodepoint.enums import UserRole
from nodepoint.models import ApiKey


class AllowAnyPermission(BasePermission):
    def has_permission(self, request, view):
        return True


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user_role(user) == UserRole.SUPERADMIN)


class IsAdminOrAbove(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return user_role(user) in (UserRole.SUPERADMIN, UserRole.ADMIN)


class ApiKeyScopePermission(BasePermission):
    """Enforce API scopes for API keys and JWT sessions."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        url_name = getattr(request.resolver_match, "url_name", None) if request.resolver_match else None
        required = scope_for_request(url_name, request.method)
        if required is None:
            return True
        api_key: ApiKey | None = getattr(request, "auth_api_key", None)
        if api_key is None and isinstance(request.auth, ApiKey):
            api_key = request.auth
        if api_key is not None:
            allowed = set(api_key.allowed_scopes or [])
            return required in allowed
        return user_has_scope(user, required)
