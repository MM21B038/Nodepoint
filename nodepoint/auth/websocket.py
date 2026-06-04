from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()


def _extract_token_from_scope(scope: dict) -> str | None:
    headers = dict(scope.get("headers") or [])
    auth_header = headers.get(b"authorization", b"").decode()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    raw = scope.get("query_string") or b""
    if isinstance(raw, bytes):
        raw = raw.decode()
    params = parse_qs(raw)
    token_vals = params.get("token") or []
    if token_vals:
        return token_vals[0].strip()
    return None


@database_sync_to_async
def authenticate_websocket(scope: dict):
    token = _extract_token_from_scope(scope)
    if not token:
        return AnonymousUser(), "Authentication required"
    try:
        validated = AccessToken(token)
        user_id = validated["user_id"]
        user = User.objects.get(pk=user_id, is_active=True)
        return user, None
    except (TokenError, InvalidToken, User.DoesNotExist, KeyError):
        return AnonymousUser(), "Invalid or expired token"
