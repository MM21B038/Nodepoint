from __future__ import annotations

from typing import Any
from uuid import UUID

from rapidfuzz import fuzz

from nodepoint.models import KnowledgeEntity, Workspace
from nodepoint.services import kg_graph
from nodepoint.services.kg_graph import GraphFilters, parse_graph_filters
from nodepoint.services.workspace import get_flagged_workspaces_qs

DEFAULT_MATCH_LIMIT = 20
MAX_MATCH_LIMIT = 100
DEFAULT_FUZZY_THRESHOLD = 0.6
CANDIDATE_PREFETCH_LIMIT = 3000


def parse_entity_search_params(
    *,
    depth_raw: str | None,
    limit_raw: str | None,
    entity_type_raw: str | None,
    threshold_raw: str | None,
    match_limit_raw: str | None,
) -> tuple[GraphFilters, float, int]:
    filters = parse_graph_filters(
        entity_type_raw=entity_type_raw,
        depth_raw=depth_raw,
        limit_raw=limit_raw,
    )
    threshold = DEFAULT_FUZZY_THRESHOLD
    if threshold_raw is not None and str(threshold_raw).strip():
        try:
            threshold = float(threshold_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("threshold must be a number between 0 and 1") from exc
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError("threshold must be between 0 and 1")

    match_limit = DEFAULT_MATCH_LIMIT
    if match_limit_raw is not None and str(match_limit_raw).strip():
        try:
            match_limit = int(match_limit_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("match_limit must be an integer") from exc
        if match_limit < 1 or match_limit > MAX_MATCH_LIMIT:
            raise ValueError(f"match_limit must be between 1 and {MAX_MATCH_LIMIT}")

    return filters, threshold, match_limit


def _candidate_entities_qs(
    workspace_names: list[str],
    query: str,
    entity_types: list[str] | None,
):
    qs = KnowledgeEntity.objects.filter(
        document__workspace__name__in=workspace_names,
    ).select_related("document", "document__workspace")
    if entity_types is not None:
        qs = qs.filter(entity_type__in=entity_types)
    q = query.strip()
    if len(q) >= 2:
        qs = qs.filter(name__icontains=q)
    return qs.order_by("created_at", "id")[:CANDIDATE_PREFETCH_LIMIT]


def fuzzy_match_entities(
    query: str,
    workspace_names: list[str],
    *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
    match_limit: int = DEFAULT_MATCH_LIMIT,
    entity_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    q = query.strip()
    if not q or not workspace_names:
        return []

    candidates = list(
        _candidate_entities_qs(workspace_names, q, entity_types)
    )
    if not candidates and len(q) >= 1:
        qs = KnowledgeEntity.objects.filter(
            document__workspace__name__in=workspace_names,
        ).select_related("document", "document__workspace")
        if entity_types is not None:
            qs = qs.filter(entity_type__in=entity_types)
        candidates = list(qs.order_by("created_at", "id")[:CANDIDATE_PREFETCH_LIMIT])

    scored: list[tuple[KnowledgeEntity, float]] = []
    for entity in candidates:
        score = fuzz.token_set_ratio(q, entity.name or "") / 100.0
        if score >= threshold:
            scored.append((entity, score))

    scored.sort(key=lambda pair: (-pair[1], pair[0].name or "", str(pair[0].id)))
    top = scored[:match_limit]

    results: list[dict[str, Any]] = []
    for entity, score in top:
        row = kg_graph.serialize_entity(entity)
        row["score"] = round(score, 4)
        row["workspace"] = entity.document.workspace.name
        results.append(row)
    return results


def search_workspace_by_name(
    workspace: Workspace,
    query: str,
    graph_filters: GraphFilters,
    *,
    threshold: float,
    match_limit: int,
) -> dict[str, Any]:
    matches = fuzzy_match_entities(
        query,
        [workspace.name],
        threshold=threshold,
        match_limit=match_limit,
        entity_types=graph_filters.entity_types,
    )
    seed_ids = [UUID(m["id"]) for m in matches]
    graph = kg_graph.build_graph_from_seed_ids(
        workspace,
        seed_ids,
        depth=graph_filters.depth,
        limit=graph_filters.limit,
        entity_types=graph_filters.entity_types,
    )
    graph_filters_dict = graph_filters.as_response_dict()
    graph_filters_dict["threshold"] = threshold
    graph_filters_dict["match_limit"] = match_limit
    return {
        "workspace": workspace.name,
        "query": query.strip(),
        "filters": graph_filters_dict,
        "matches": matches,
        "graph": graph,
    }


def search_flagged_workspaces_by_name(
    query: str,
    graph_filters: GraphFilters,
    *,
    threshold: float,
    match_limit: int,
) -> dict[str, Any]:
    workspaces = list(get_flagged_workspaces_qs().order_by("name"))
    graph_filters_dict = graph_filters.as_response_dict()
    graph_filters_dict["threshold"] = threshold
    graph_filters_dict["match_limit"] = match_limit

    results = []
    for ws in workspaces:
        matches = fuzzy_match_entities(
            query,
            [ws.name],
            threshold=threshold,
            match_limit=match_limit,
            entity_types=graph_filters.entity_types,
        )
        seed_ids = [UUID(m["id"]) for m in matches]
        graph = kg_graph.build_graph_from_seed_ids(
            ws,
            seed_ids,
            depth=graph_filters.depth,
            limit=graph_filters.limit,
            entity_types=graph_filters.entity_types,
        )
        results.append(
            {
                "workspace": ws.name,
                "matches": matches,
                "graph": graph,
            }
        )

    return {
        "query": query.strip(),
        "filters": graph_filters_dict,
        "workspaces": results,
    }


def search_by_name_for_workspace_name(
    name: str,
    query: str,
    graph_filters: GraphFilters,
    *,
    threshold: float,
    match_limit: int,
) -> dict[str, Any]:
    workspace = Workspace.objects.get(name=name)
    return search_workspace_by_name(
        workspace,
        query,
        graph_filters,
        threshold=threshold,
        match_limit=match_limit,
    )
