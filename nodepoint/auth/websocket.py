from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple, cast
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from nodepoint.auth.users import User


def _extract_token_from_scope(scope: Mapping[str, Any]) -> str | None:
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


def _authenticate_websocket_sync(
    scope: Mapping[str, Any],
) -> Tuple[User | None, str | None]:
    token = _extract_token_from_scope(scope)
    if not token:
        return None, "Authentication required"
    try:
        # simplejwt annotates __init__ as Token | None; encoded JWTs are str.
        validated = AccessToken(cast(Any, token))
        user_id = validated["user_id"]
        user = User.objects.get(pk=user_id, is_active=True)
        return user, None
    except (TokenError, InvalidToken, User.DoesNotExist, KeyError):
        return None, "Invalid or expired token"


authenticate_websocket = database_sync_to_async(_authenticate_websocket_sync)
