from __future__ import annotations
from typing import Any, Dict, List, Tuple

from django.db.models import Count, QuerySet

from nodepoint.auth.users import User
from nodepoint.models import (
    Document,
    DocumentChunk,
    KnowledgeEntity,
    KnowledgeRelation,
    Workspace,
)
from nodepoint.services import optional_fields as opt
from nodepoint.services.workspace_group import user_workspaces_qs

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

_EMPTY_COUNTS = {"files": 0, "chunks": 0, "entities": 0, "relations": 0}


def get_workspace_count_stats(*, actor: User) -> Dict[str, Any]:
    base = user_workspaces_qs(actor)
    total = base.count()
    in_group = (
        base.filter(group_memberships__isnull=False)
        .distinct()
        .count()
    )
    return {
        "total": total,
        "in_group": in_group,
        "ungrouped": total - in_group,
    }


def parse_group_filter(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip()


def filter_workspaces_by_group(
    qs: QuerySet[Workspace],
    group_name: str | None,
    *,
    group_owner_id: int | None = None,
) -> QuerySet[Workspace]:
    if not group_name:
        return qs
    qs = qs.filter(group_memberships__group__name=group_name)
    if group_owner_id is not None:
        qs = qs.filter(group_memberships__group__owner_id=group_owner_id)
    return qs.distinct()


def workspaces_base_qs(
    actor: User,
    group_name: str | None = None,
    *,
    group_owner_id: int | None = None,
) -> QuerySet[Workspace]:
    return filter_workspaces_by_group(
        user_workspaces_qs(actor)
        .select_related("owner")
        .order_by("-created_at", "name"),
        group_name,
        group_owner_id=group_owner_id,
    )


def bulk_counts_for_workspace_ids(workspace_ids: List[int]) -> Dict[int, Dict[str, int]]:
    """Per-workspace file/chunk/entity/relation counts (page-sized batches only)."""
    if not workspace_ids:
        return {}

    result = {wid: dict(_EMPTY_COUNTS) for wid in workspace_ids}

    for wid, count in (
        Document.objects.filter(workspace_id__in=workspace_ids)
        .values("workspace_id")
        .annotate(c=Count("id"))
        .values_list("workspace_id", "c")
    ):
        result[wid]["files"] = count

    for wid, count in (
        DocumentChunk.objects.filter(document__workspace_id__in=workspace_ids)
        .values("document__workspace_id")
        .annotate(c=Count("id"))
        .values_list("document__workspace_id", "c")
    ):
        result[wid]["chunks"] = count

    for wid, count in (
        KnowledgeEntity.objects.filter(document__workspace_id__in=workspace_ids)
        .values("document__workspace_id")
        .annotate(c=Count("id"))
        .values_list("document__workspace_id", "c")
    ):
        result[wid]["entities"] = count

    for wid, count in (
        KnowledgeRelation.objects.filter(document__workspace_id__in=workspace_ids)
        .values("document__workspace_id")
        .annotate(c=Count("id"))
        .values_list("document__workspace_id", "c")
    ):
        result[wid]["relations"] = count

    return result


def serialize_workspace_row(
    ws: Workspace,
    *,
    counts: Dict[str, int] | None = None,
) -> Dict[str, Any]:
    groups = sorted(m.group.name for m in ws.group_memberships.all())
    row = {
        "id": ws.pk,
        "name": ws.name,
        "owner_id": ws.owner_id,
        "owner_username": ws.owner.username,
        "tag": opt.optional_field_for_api(ws.tag),
        "description": opt.optional_field_for_api(ws.description),
        "groups": groups,
        "created_at": ws.created_at,
    }
    if counts is not None:
        row["counts"] = counts
    return row


def parse_pagination(
    page_raw: str | None,
    page_size_raw: str | None,
) -> Tuple[int, int]:
    page = 1
    if page_raw is not None and str(page_raw).strip():
        try:
            page = int(page_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("page must be a positive integer") from exc
        if page < 1:
            raise ValueError("page must be a positive integer")

    page_size = DEFAULT_PAGE_SIZE
    if page_size_raw is not None and str(page_size_raw).strip():
        try:
            page_size = int(page_size_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("page_size must be an integer") from exc
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    return page, page_size


def parse_include_counts(raw: str | None) -> bool:
    if raw is None or not str(raw).strip():
        return True
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    raise ValueError("include_counts must be true or false")


def paginate_queryset(
    qs: QuerySet,
    *,
    page: int,
    page_size: int,
) -> Tuple[List[Any], Dict[str, Any]]:
    total_items = qs.count()
    total_pages = max(1, (total_items + page_size - 1) // page_size) if total_items else 1
    page = min(max(1, page), total_pages)
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    return items, {
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_previous": page > 1,
    }


def list_workspaces_paginated(
    *,
    actor: User,
    group_name: str | None = None,
    group_owner_id: int | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    include_counts: bool = True,
) -> Dict[str, Any]:
    base = workspaces_base_qs(
        actor, group_name, group_owner_id=group_owner_id
    ).prefetch_related(
        "group_memberships__group"
    )
    page_list, pagination = paginate_queryset(
        base, page=page, page_size=page_size
    )

    counts_by_id: Dict[int, Dict[str, int]] = {}
    if include_counts and page_list:
        counts_by_id = bulk_counts_for_workspace_ids([ws.pk for ws in page_list])

    items = [
        serialize_workspace_row(
            ws,
            counts=counts_by_id.get(ws.pk, _EMPTY_COUNTS) if include_counts else None,
        )
        for ws in page_list
    ]
    return {
        "group": group_name,
        "include_counts": include_counts,
        "pagination": pagination,
        "workspaces": items,
    }
