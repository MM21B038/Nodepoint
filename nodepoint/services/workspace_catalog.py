from __future__ import annotations

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, QuerySet

from nodepoint.models import Workspace
from nodepoint.services.workspace import FLAGGED_CHAT_WORKSPACE_NAME

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def user_workspaces_qs() -> QuerySet[Workspace]:
    return Workspace.objects.exclude(name=FLAGGED_CHAT_WORKSPACE_NAME)


def get_workspace_count_stats() -> dict:
    qs = user_workspaces_qs()
    total = qs.count()
    flagged = qs.filter(is_flag=True).count()
    return {
        "total": total,
        "flagged": flagged,
        "non_flagged": total - flagged,
    }


def parse_flag_filter(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return "all"
    value = str(raw).strip().lower()
    aliases = {
        "all": "all",
        "flagged": "flagged",
        "starred": "flagged",
        "true": "flagged",
        "1": "flagged",
        "non_flagged": "non_flagged",
        "non-flagged": "non_flagged",
        "unflagged": "non_flagged",
        "not_flagged": "non_flagged",
        "false": "non_flagged",
        "0": "non_flagged",
    }
    if value not in aliases:
        raise ValueError(
            "flag must be all, flagged, or non_flagged"
        )
    return aliases[value]


def filter_workspaces_by_flag(qs: QuerySet[Workspace], flag_filter: str) -> QuerySet[Workspace]:
    if flag_filter == "flagged":
        return qs.filter(is_flag=True)
    if flag_filter == "non_flagged":
        return qs.filter(is_flag=False)
    return qs


def workspaces_with_counts_qs() -> QuerySet[Workspace]:
    return user_workspaces_qs().annotate(
        file_count=Count("documents", distinct=True),
        chunk_count=Count("documents__chunks", distinct=True),
        entity_count=Count("documents__entities", distinct=True),
        relation_count=Count("documents__relations", distinct=True),
    )


def serialize_workspace_row(ws: Workspace) -> dict:
    return {
        "name": ws.name,
        "is_flag": ws.is_flag,
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
    flag_filter: str = "all",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    qs = filter_workspaces_by_flag(
        workspaces_with_counts_qs().order_by("-created_at", "name"),
        flag_filter,
    )
    paginator = Paginator(qs, page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else paginator.page(1)

    items = [serialize_workspace_row(ws) for ws in page_obj.object_list]
    return {
        "filter": flag_filter,
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
