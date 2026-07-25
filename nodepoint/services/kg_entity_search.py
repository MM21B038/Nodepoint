from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple
from uuid import UUID

from rapidfuzz import fuzz

from nodepoint.models import Document, KnowledgeEntity, KnowledgeRelation, Workspace
from nodepoint.services import kg_graph
from nodepoint.services.group_scope import resolve_group_search_scope
from nodepoint.services.kg_graph import GraphFilters, parse_graph_filters
from nodepoint.services.workspace_group import get_group_workspaces_qs
from nodepoint.enums import GroupTag

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
    file_name_raw: str | None = None,
    threshold_raw: str | None,
    match_limit_raw: str | None,
) -> Tuple[GraphFilters, float, int]:
    filters = parse_graph_filters(
        entity_type_raw=entity_type_raw,
        file_name_raw=file_name_raw,
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
    workspace_names: List[str],
    entity_types: List[str] | None,
    file_names: List[str] | None = None,
    document_ids: List[UUID] | None = None,
):
    qs = KnowledgeEntity.objects.filter(
        document__workspace__name__in=workspace_names,
    ).select_related("document", "document__workspace")
    if entity_types is not None:
        qs = qs.filter(entity_type__in=entity_types)
    if file_names is not None:
        qs = qs.filter(document__file_name__in=file_names)
    if document_ids is not None:
        qs = qs.filter(document_id__in=document_ids)
    return qs


def _query_tokens(query: str) -> List[str]:
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
    into: Dict[UUID, KnowledgeEntity],
    entities: List[KnowledgeEntity],
    *,
    cap: int,
) -> None:
    for entity in entities:
        if entity.id not in into:
            into[entity.id] = entity
        if len(into) >= cap:
            break


def gather_entity_candidates(
    workspace_names: List[str],
    query: str,
    entity_types: List[str] | None,
    file_names: List[str] | None = None,
    *,
    document_ids: List[UUID] | None = None,
    include_broad_sample: bool = True,
) -> List[KnowledgeEntity]:
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

    base = _base_entity_qs(
        workspace_names, entity_types, file_names, document_ids=document_ids
    )
    merged: Dict[UUID, KnowledgeEntity] = {}

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
    workspace_names: List[str],
    *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
    match_limit: int = DEFAULT_MATCH_LIMIT,
    entity_types: List[str] | None = None,
    file_names: List[str] | None = None,
    document_ids: List[UUID] | None = None,
    entity_ids: List[UUID] | None = None,
) -> List[Dict[str, Any]]:
    q = query.strip()
    if not q or not workspace_names:
        return []

    candidates = gather_entity_candidates(
        workspace_names,
        q,
        entity_types,
        file_names,
        document_ids=document_ids,
        include_broad_sample=True,
    )
    if entity_ids is not None:
        allowed = {str(entity_id) for entity_id in entity_ids}
        candidates = [entity for entity in candidates if str(entity.id) in allowed]
    scored = _rank_entities(q, candidates, threshold=threshold)
    top = scored[:match_limit]

    results: List[Dict[str, Any]] = []
    for entity, score in top:
        row = kg_graph.serialize_graph_node(entity)
        row["score"] = round(score, 4)
        row["workspace"] = entity.document.workspace.name
        results.append(row)
    return results


def _rank_entities(
    query: str,
    candidates: List[KnowledgeEntity],
    *,
    threshold: float,
) -> List[Tuple[KnowledgeEntity, float]]:
    scored: List[Tuple[KnowledgeEntity, float]] = []
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
) -> Dict[str, Any]:
    matches = fuzzy_match_entities(
        query,
        [workspace.name],
        threshold=threshold,
        match_limit=match_limit,
        entity_types=graph_filters.entity_types,
        file_names=graph_filters.file_names,
    )
    seed_ids = [UUID(m["id"]) for m in matches]
    graph = kg_graph.build_graph_from_seed_ids(
        workspace,
        seed_ids,
        depth=graph_filters.depth,
        limit=graph_filters.limit,
        entity_types=graph_filters.entity_types,
        file_names=graph_filters.file_names,
        filters=graph_filters,
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


def _group_search_result_row(
    workspace: Workspace,
    query: str,
    graph_filters: GraphFilters,
    *,
    threshold: float,
    match_limit: int,
    file_names: List[str] | None = None,
    document_ids: List[UUID] | None = None,
    entity_ids: List[UUID] | None = None,
) -> Dict[str, Any]:
    matches = fuzzy_match_entities(
        query,
        [workspace.name],
        threshold=threshold,
        match_limit=match_limit,
        entity_types=graph_filters.entity_types,
        file_names=file_names if file_names is not None else graph_filters.file_names,
        document_ids=document_ids,
        entity_ids=entity_ids,
    )
    seed_ids = [UUID(m["id"]) for m in matches]
    graph = kg_graph.build_graph_from_seed_ids(
        workspace,
        seed_ids,
        depth=graph_filters.depth,
        limit=graph_filters.limit,
        entity_types=graph_filters.entity_types,
        file_names=file_names if file_names is not None else graph_filters.file_names,
        filters=graph_filters,
    )
    return {
        "workspace": workspace.name,
        "matches": matches,
        "graph": graph,
    }


def search_group_workspaces_by_name(
    group_name: str,
    query: str,
    graph_filters: GraphFilters,
    *,
    threshold: float,
    match_limit: int,
    actor=None,
    owner_id: int | None = None,
) -> Dict[str, Any]:
    scope = resolve_group_search_scope(
        group_name, actor=actor, owner_id=owner_id
    )
    graph_filters_dict = graph_filters.as_response_dict()
    graph_filters_dict["threshold"] = threshold
    graph_filters_dict["match_limit"] = match_limit

    results: List[Dict[str, Any]] = []

    if scope.tag == GroupTag.WORKSPACE:
        workspaces = list(
            get_group_workspaces_qs(
                group_name, actor=actor, owner_id=owner_id
            ).order_by("name")
        )
        for ws in workspaces:
            results.append(
                _group_search_result_row(
                    ws,
                    query,
                    graph_filters,
                    threshold=threshold,
                    match_limit=match_limit,
                )
            )

    elif scope.tag == GroupTag.FILES:
        doc_ids_by_ws_id: Dict[int, List[UUID]] = {}
        workspace_by_id: Dict[int, Workspace] = {}
        for doc in Document.objects.filter(id__in=scope.document_ids).select_related(
            "workspace"
        ):
            ws = doc.workspace
            workspace_by_id[ws.pk] = ws
            doc_ids_by_ws_id.setdefault(ws.pk, []).append(doc.id)
        for ws_id in sorted(doc_ids_by_ws_id):
            workspace = workspace_by_id[ws_id]
            doc_ids = doc_ids_by_ws_id[ws_id]
            member_file_names = list(
                Document.objects.filter(id__in=doc_ids).values_list(
                    "file_name", flat=True
                )
            )
            file_names = member_file_names
            if graph_filters.file_names is not None:
                file_names = [
                    name for name in member_file_names if name in graph_filters.file_names
                ]
            results.append(
                _group_search_result_row(
                    workspace,
                    query,
                    graph_filters,
                    threshold=threshold,
                    match_limit=match_limit,
                    file_names=file_names or None,
                    document_ids=doc_ids,
                )
            )

    elif scope.tag == GroupTag.ENTITY:
        entity_ids_by_ws_id: Dict[int, List[UUID]] = {}
        workspace_by_id: Dict[int, Workspace] = {}
        for entity in KnowledgeEntity.objects.filter(
            id__in=scope.entity_ids
        ).select_related("document__workspace"):
            ws = entity.document.workspace
            workspace_by_id[ws.pk] = ws
            entity_ids_by_ws_id.setdefault(ws.pk, []).append(entity.id)
        for ws_id in sorted(entity_ids_by_ws_id):
            workspace = workspace_by_id[ws_id]
            results.append(
                _group_search_result_row(
                    workspace,
                    query,
                    graph_filters,
                    threshold=threshold,
                    match_limit=match_limit,
                    entity_ids=entity_ids_by_ws_id[ws_id],
                )
            )

    else:
        relation_ids_by_ws_id: Dict[int, List[UUID]] = {}
        endpoint_ids_by_ws_id: Dict[int, List[UUID]] = {}
        workspace_by_id: Dict[int, Workspace] = {}
        for relation in KnowledgeRelation.objects.filter(
            id__in=scope.relation_ids
        ).select_related("document__workspace"):
            ws = relation.document.workspace
            workspace_by_id[ws.pk] = ws
            relation_ids_by_ws_id.setdefault(ws.pk, []).append(relation.id)
            endpoint_ids_by_ws_id.setdefault(ws.pk, []).extend(
                [relation.source_id, relation.target_id]
            )
        for ws_id in sorted(relation_ids_by_ws_id):
            workspace = workspace_by_id[ws_id]
            endpoint_ids = list(set(endpoint_ids_by_ws_id.get(ws_id, [])))
            results.append(
                _group_search_result_row(
                    workspace,
                    query,
                    graph_filters,
                    threshold=threshold,
                    match_limit=match_limit,
                    entity_ids=endpoint_ids,
                )
            )

    return {
        "query": query.strip(),
        "group": group_name,
        "tag": scope.tag,
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
) -> Dict[str, Any]:
    workspace = Workspace.objects.get(name=name)
    return search_workspace_by_name(
        workspace,
        query,
        graph_filters,
        threshold=threshold,
        match_limit=match_limit,
    )
