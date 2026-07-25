from __future__ import annotations

from django.utils import timezone
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication as SimpleJWTAuth

from nodepoint.auth.account_lifecycle import login_block_message, profile_allows_login
from nodepoint.auth.api_keys import lookup_api_key
from nodepoint.auth.users import ensure_profile
from nodepoint.models import ApiKey


class JWTAuthentication(SimpleJWTAuth):
    """JWT bearer tokens for interactive users."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        profile = ensure_profile(user)
        if not profile_allows_login(profile):
            msg = login_block_message(profile) or "Account cannot access the API."
            raise AuthenticationFailed(msg, code="account_blocked")
        return user


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """API keys via Authorization: Api-Key <key> or X-Api-Key header."""

    keyword = "Api-Key"

    def authenticate(self, request):
        raw = self._extract_key(request)
        if not raw:
            return None
        api_key = lookup_api_key(raw)
        if api_key is None:
            raise AuthenticationFailed("Invalid or expired API key")
        profile = ensure_profile(api_key.user)
        if not profile_allows_login(profile):
            msg = login_block_message(profile) or "Account cannot access the API."
            raise AuthenticationFailed(msg, code="account_blocked")
        ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        setattr(request, "auth_api_key", api_key)
        return (api_key.user, api_key)

    def _extract_key(self, request) -> str | None:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if auth:
            parts = auth.split()
            if len(parts) == 2 and parts[0] == self.keyword:
                return parts[1].strip()
        header = request.META.get("HTTP_X_API_KEY", "")
        if header:
            return header.strip()
        return None
