"""Resolve resource owner hints from API query/body (disambiguate same names per owner)."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from nodepoint.auth.visibility import can_access_owner

User = get_user_model()


class OwnerScopeError(ValueError):
    pass


class OwnerNotAccessibleError(OwnerScopeError):
    pass


def parse_owner_id(
    *,
    actor,
    owner_id_raw: str | int | None = None,
    owner_username: str | None = None,
) -> int | None:
    """Return owner user id from hint, or None when not specified."""
    if owner_id_raw is not None and str(owner_id_raw).strip() != "":
        try:
            owner_id = int(owner_id_raw)
        except (TypeError, ValueError) as exc:
            raise OwnerScopeError("owner_id must be an integer") from exc
        if not can_access_owner(actor, owner_id):
            raise OwnerNotAccessibleError("owner_id is not accessible")
        return owner_id

    username = (owner_username or "").strip()
    if not username:
        return None

    try:
        owner = User.objects.get(username=username)
    except User.DoesNotExist as exc:
        raise OwnerScopeError(f"Unknown owner username: {username}") from exc
    if not can_access_owner(actor, owner.pk):
        raise OwnerNotAccessibleError("owner_username is not accessible")
    return owner.pk


def parse_owner_id_from_query_dict(
    params: dict[str, list[str]],
    *,
    actor,
) -> int | None:
    """Parse owner hints from URL query (e.g. WebSocket connect)."""
    owner_id_vals = params.get("owner_id") or []
    owner_id_raw = owner_id_vals[0] if owner_id_vals else None
    username_vals = params.get("owner_username") or []
    owner_username = username_vals[0] if username_vals else None
    return parse_owner_id(
        actor=actor,
        owner_id_raw=owner_id_raw,
        owner_username=owner_username,
    )


def parse_owner_id_from_request(request) -> int | None:
    params = getattr(request, "query_params", None) or {}
    data = getattr(request, "data", None) or {}
    owner_id_raw = params.get("owner_id")
    if owner_id_raw is None and isinstance(data, dict):
        owner_id_raw = data.get("owner_id")
    owner_username = params.get("owner_username")
    if owner_username is None and isinstance(data, dict):
        owner_username = data.get("owner_username")
    return parse_owner_id(
        actor=request.user,
        owner_id_raw=owner_id_raw,
        owner_username=owner_username,
    )
