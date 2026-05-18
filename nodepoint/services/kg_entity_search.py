from __future__ import annotations

import re
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
# When icontains prefilter fills this many rows, still merge a full-workspace sample for typos.
BROADEN_PREFILTER_AT = 500

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


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


def _base_entity_qs(
    workspace_names: list[str],
    entity_types: list[str] | None,
):
    qs = KnowledgeEntity.objects.filter(
        document__workspace__name__in=workspace_names,
    ).select_related("document", "document__workspace")
    if entity_types is not None:
        qs = qs.filter(entity_type__in=entity_types)
    return qs


def _query_tokens(query: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= 2]


def score_entity_name(query: str, name: str) -> float:
    """Best fuzzy score for entity name matching (0–1)."""
    q = (query or "").strip()
    n = (name or "").strip()
    if not q or not n:
        return 0.0
    if q.lower() == n.lower():
        return 1.0

    scores = [
        fuzz.WRatio(q, n) / 100.0,
        fuzz.partial_ratio(q, n) / 100.0,
        fuzz.token_set_ratio(q, n) / 100.0,
        fuzz.ratio(q, n) / 100.0,
    ]
    # Substring / abbreviation: short query inside longer name
    n_lower = n.lower()
    q_lower = q.lower()
    if len(q_lower) >= 2 and q_lower in n_lower:
        scores.append(min(1.0, len(q_lower) / max(len(n_lower), 1) + 0.55))
    return max(scores)


def _merge_entities(
    into: dict[UUID, KnowledgeEntity],
    entities: list[KnowledgeEntity],
    *,
    cap: int,
) -> None:
    for entity in entities:
        if entity.id not in into:
            into[entity.id] = entity
        if len(into) >= cap:
            break


def gather_entity_candidates(
    workspace_names: list[str],
    query: str,
    entity_types: list[str] | None,
    *,
    include_broad_sample: bool = True,
) -> list[KnowledgeEntity]:
    """
  Build a candidate pool for fuzzy scoring.

  icontains alone misses typos (e.g. Alciedoes not match Alice). We union:
  - full query icontains
  - per-token icontains
  - istartswith on first 2–3 characters
  - optional full-workspace sample (ordered by name) for typo recovery
    """
    q = query.strip()
    if not q:
        return []

    base = _base_entity_qs(workspace_names, entity_types)
    merged: dict[UUID, KnowledgeEntity] = {}

    if len(q) >= 2:
        _merge_entities(merged, list(base.filter(name__icontains=q)[:CANDIDATE_PREFETCH_LIMIT]), cap=CANDIDATE_PREFETCH_LIMIT)

        prefix_len = min(3, len(q))
        _merge_entities(
            merged,
            list(base.filter(name__istartswith=q[:prefix_len])[:CANDIDATE_PREFETCH_LIMIT]),
            cap=CANDIDATE_PREFETCH_LIMIT,
        )

        for token in _query_tokens(q):
            _merge_entities(
                merged,
                list(base.filter(name__icontains=token)[:CANDIDATE_PREFETCH_LIMIT]),
                cap=CANDIDATE_PREFETCH_LIMIT,
            )

    # Typo-tolerant: always add a name-ordered sample so entities missed by icontains are scored.
    if include_broad_sample and (
        len(merged) < 50 or len(merged) >= BROADEN_PREFILTER_AT
    ):
        remaining = CANDIDATE_PREFETCH_LIMIT - len(merged)
        if remaining > 0:
            _merge_entities(
                merged,
                list(base.order_by("name", "id")[:remaining]),
                cap=CANDIDATE_PREFETCH_LIMIT,
            )

    if not merged:
        _merge_entities(
            merged,
            list(base.order_by("name", "id")[:CANDIDATE_PREFETCH_LIMIT]),
            cap=CANDIDATE_PREFETCH_LIMIT,
        )

    return list(merged.values())


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

    candidates = gather_entity_candidates(
        workspace_names, q, entity_types, include_broad_sample=True
    )
    scored = _rank_entities(q, candidates, threshold=threshold)
    top = scored[:match_limit]

    results: list[dict[str, Any]] = []
    for entity, score in top:
        row = kg_graph.serialize_entity(entity)
        row["score"] = round(score, 4)
        row["workspace"] = entity.document.workspace.name
        results.append(row)
    return results


def _rank_entities(
    query: str,
    candidates: list[KnowledgeEntity],
    *,
    threshold: float,
) -> list[tuple[KnowledgeEntity, float]]:
    scored: list[tuple[KnowledgeEntity, float]] = []
    for entity in candidates:
        score = score_entity_name(query, entity.name or "")
        if score >= threshold:
            scored.append((entity, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].name or "", str(pair[0].id)))
    return scored


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
