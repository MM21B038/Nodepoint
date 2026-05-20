from __future__ import annotations

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, QuerySet

from nodepoint.models import Workspace
from nodepoint.services.workspace_group import user_workspaces_qs

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def get_workspace_count_stats() -> dict:
    qs = user_workspaces_qs()
    total = qs.count()
    in_group = qs.filter(group_memberships__isnull=False).distinct().count()
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
    qs: QuerySet[Workspace], group_name: str | None
) -> QuerySet[Workspace]:
    if not group_name:
        return qs
    return qs.filter(group_memberships__group__name=group_name).distinct()


def workspaces_with_counts_qs() -> QuerySet[Workspace]:
    return user_workspaces_qs().annotate(
        file_count=Count("documents", distinct=True),
        chunk_count=Count("documents__chunks", distinct=True),
        entity_count=Count("documents__entities", distinct=True),
        relation_count=Count("documents__relations", distinct=True),
    )


def serialize_workspace_row(ws: Workspace) -> dict:
    groups = sorted(m.group.name for m in ws.group_memberships.all())
    return {
        "name": ws.name,
        "groups": groups,
        "created_at": ws.created_at,
        "counts": {
            "files": getattr(ws, "file_count", 0),
            "chunks": getattr(ws, "chunk_count", 0),
            "entities": getattr(ws, "entity_count", 0),
            "relations": getattr(ws, "relation_count", 0),
        },
    }


def parse_pagination(
    page_raw: str | None,
    page_size_raw: str | None,
) -> tuple[int, int]:
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


def list_workspaces_paginated(
    *,
    group_name: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    qs = filter_workspaces_by_group(
        workspaces_with_counts_qs()
        .prefetch_related("group_memberships__group")
        .order_by("-created_at", "name"),
        group_name,
    )
    paginator = Paginator(qs, page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)

    items = [serialize_workspace_row(ws) for ws in page_obj.object_list]
    return {
        "group": group_name,
        "pagination": {
            "page": page_obj.number,
            "page_size": page_size,
            "total_items": paginator.count,
            "total_pages": paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        },
        "workspaces": items,
    }
