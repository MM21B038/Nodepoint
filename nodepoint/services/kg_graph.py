from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.db.models import Count, Q

from nodepoint.enums import GroupTag
from nodepoint.models import Document, KnowledgeEntity, KnowledgeRelation, Workspace
from nodepoint.services.group_scope import GroupSearchScope, resolve_group_search_scope
from nodepoint.services.workspace_group import get_group_workspaces_qs

DEFAULT_GRAPH_LIMIT = 500
DEFAULT_GRAPH_DEPTH = 1
MAX_GRAPH_LIMIT = 5000
MAX_GRAPH_DEPTH = 5


@dataclass(frozen=True)
class GraphFilters:
    entity_types: list[str] | None
    file_names: list[str] | None
    depth: int
    limit: int

    def as_response_dict(self) -> dict[str, Any]:
        return {
            "entity_types": self.entity_types,
            "file_names": self.file_names,
            "depth": self.depth,
            "limit": self.limit,
        }


def parse_entity_types_param(raw: str | None) -> list[str] | None:
    if raw is None or not str(raw).strip():
        return None
    types = [part.strip() for part in str(raw).split(",") if part.strip()]
    if not types:
        raise ValueError("entity_type must include at least one non-empty type")
    return types


def parse_file_names_param(raw: str | None) -> list[str] | None:
    if raw is None or not str(raw).strip():
        return None
    names = [part.strip() for part in str(raw).split(",") if part.strip()]
    if not names:
        raise ValueError("file_name must include at least one non-empty file name")
    return names


def parse_graph_filters(
    *,
    entity_type_raw: str | None,
    file_name_raw: str | None = None,
    depth_raw: str | None,
    limit_raw: str | None,
) -> GraphFilters:
    entity_types = parse_entity_types_param(entity_type_raw)
    file_names = parse_file_names_param(file_name_raw)

    depth = DEFAULT_GRAPH_DEPTH
    if depth_raw is not None and str(depth_raw).strip():
        try:
            depth = int(depth_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("depth must be an integer") from exc
        if depth < 0 or depth > MAX_GRAPH_DEPTH:
            raise ValueError(f"depth must be between 0 and {MAX_GRAPH_DEPTH}")

    limit = DEFAULT_GRAPH_LIMIT
    if limit_raw is not None and str(limit_raw).strip():
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        if limit < 1 or limit > MAX_GRAPH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_GRAPH_LIMIT}")

    return GraphFilters(
        entity_types=entity_types,
        file_names=file_names,
        depth=depth,
        limit=limit,
    )


def serialize_graph_node(entity: KnowledgeEntity) -> dict:
    return {
        "id": str(entity.id),
        "name": entity.name,
        "entity_type": entity.entity_type,
    }


def serialize_edge(relation: KnowledgeRelation) -> dict:
    return {
        "id": str(relation.id),
        "source": relation.source.name,
        "target": relation.target.name,
        "source_id": str(relation.source_id),
        "target_id": str(relation.target_id),
        "type_description": relation.type_description,
    }


def _entity_types_rows(qs) -> list[dict[str, Any]]:
    rows = (
        qs.values("entity_type")
        .annotate(count=Count("id"))
        .order_by("-count", "entity_type")
    )
    return [{"type": row["entity_type"], "count": row["count"]} for row in rows]


def list_entity_types_for_workspace(workspace: Workspace) -> dict:
    qs = KnowledgeEntity.objects.filter(document__workspace=workspace)
    return {
        "workspace": workspace.name,
        "entity_types": _entity_types_rows(qs),
    }


def list_entity_types_for_workspace_name(name: str) -> dict:
    workspace = Workspace.objects.get(name=name)
    return list_entity_types_for_workspace(workspace)


def list_entity_types_for_group(group_name: str) -> dict:
    scope = resolve_group_search_scope(group_name)
    workspaces = (
        get_group_workspaces_qs(group_name).order_by("name")
        if scope.workspace_names
        else Workspace.objects.none()
    )
    return {
        "group": group_name,
        "tag": scope.tag,
        "workspaces": [
            {
                "workspace": ws.name,
                "entity_types": _entity_types_rows(
                    _scoped_entity_qs(ws, scope)
                ),
            }
            for ws in workspaces
        ],
    }


def _scoped_entity_qs(workspace: Workspace, scope: GroupSearchScope):
    qs = KnowledgeEntity.objects.filter(document__workspace=workspace)
    if scope.tag == GroupTag.FILES and scope.document_ids:
        qs = qs.filter(document_id__in=scope.document_ids)
    elif scope.tag == GroupTag.ENTITY and scope.entity_ids:
        qs = qs.filter(id__in=scope.entity_ids)
    elif scope.tag == GroupTag.RELATION and scope.relation_ids:
        relation_qs = KnowledgeRelation.objects.filter(
            id__in=scope.relation_ids,
            document__workspace=workspace,
        )
        endpoint_ids = set()
        for rel in relation_qs.only("source_id", "target_id"):
            endpoint_ids.add(rel.source_id)
            endpoint_ids.add(rel.target_id)
        qs = qs.filter(id__in=endpoint_ids) if endpoint_ids else qs.none()
    return qs


def _seed_entities_qs(
    workspace: Workspace,
    entity_types: list[str] | None,
    file_names: list[str] | None = None,
):
    qs = (
        KnowledgeEntity.objects.filter(document__workspace=workspace)
        .select_related("document")
        .order_by("created_at", "id")
    )
    if entity_types is not None:
        qs = qs.filter(entity_type__in=entity_types)
    if file_names is not None:
        qs = qs.filter(document__file_name__in=file_names)
    return qs


def _fetch_neighbors(
    workspace: Workspace,
    frontier_ids: set[UUID],
) -> list[KnowledgeRelation]:
    if not frontier_ids:
        return []
    return list(
        KnowledgeRelation.objects.filter(document__workspace=workspace)
        .filter(Q(source_id__in=frontier_ids) | Q(target_id__in=frontier_ids))
        .select_related("source", "target", "document")
        .distinct()
    )


def build_graph_from_seed_ids(
    workspace: Workspace,
    seed_ids: list[UUID],
    *,
    depth: int,
    limit: int,
    entity_types: list[str] | None = None,
    file_names: list[str] | None = None,
    filters: GraphFilters | None = None,
) -> dict:
    """BFS subgraph from explicit seed entity ids (empty seeds → empty graph)."""
    if filters is None:
        filters = GraphFilters(
            entity_types=entity_types,
            file_names=file_names,
            depth=depth,
            limit=limit,
        )
    if not seed_ids:
        return {
            "workspace": workspace.name,
            "filters": filters.as_response_dict(),
            "truncated": False,
            "nodes": [],
            "edges": [],
        }

    seed_qs = (
        KnowledgeEntity.objects.filter(
            document__workspace=workspace,
            id__in=seed_ids,
        )
        .select_related("document")
        .order_by("created_at", "id")
    )
    if entity_types is not None:
        seed_qs = seed_qs.filter(entity_type__in=entity_types)
    if file_names is not None:
        seed_qs = seed_qs.filter(document__file_name__in=file_names)
    all_seeds = list(seed_qs)

    truncated = False
    if len(all_seeds) > limit:
        truncated = True
        seeds = all_seeds[:limit]
    else:
        seeds = all_seeds

    nodes_by_id: dict[UUID, KnowledgeEntity] = {e.id: e for e in seeds}
    frontier_ids: set[UUID] = set(nodes_by_id)

    for _hop in range(depth):
        if len(nodes_by_id) >= limit:
            truncated = True
            break
        if not frontier_ids:
            break

        relations = _fetch_neighbors(workspace, frontier_ids)
        next_frontier: set[UUID] = set()
        for rel in relations:
            for entity in (rel.source, rel.target):
                eid = entity.id
                if eid in nodes_by_id:
                    continue
                if len(nodes_by_id) >= limit:
                    truncated = True
                    break
                nodes_by_id[eid] = entity
                next_frontier.add(eid)
            if truncated:
                break
        if truncated:
            break
        frontier_ids = next_frontier

    node_ids = set(nodes_by_id)
    edges: list[dict] = []
    seen_edge_ids: set[UUID] = set()
    if node_ids:
        relations = (
            KnowledgeRelation.objects.filter(document__workspace=workspace)
            .filter(source_id__in=node_ids, target_id__in=node_ids)
            .select_related("source", "target")
            .order_by("created_at", "id")
        )
        for rel in relations:
            if rel.id in seen_edge_ids:
                continue
            seen_edge_ids.add(rel.id)
            edges.append(serialize_edge(rel))

    ordered_nodes = sorted(
        nodes_by_id.values(),
        key=lambda e: (e.created_at, e.id),
    )
    return {
        "workspace": workspace.name,
        "filters": filters.as_response_dict(),
        "truncated": truncated,
        "nodes": [serialize_graph_node(e) for e in ordered_nodes],
        "edges": edges,
    }


def build_filtered_workspace_graph(
    workspace: Workspace,
    filters: GraphFilters,
) -> dict:
    seed_qs = _seed_entities_qs(
        workspace, filters.entity_types, filters.file_names
    )
    all_seeds = list(seed_qs)
    truncated = False

    if len(all_seeds) > filters.limit:
        truncated = True
        seeds = all_seeds[: filters.limit]
    else:
        seeds = all_seeds

    nodes_by_id: dict[UUID, KnowledgeEntity] = {e.id: e for e in seeds}
    frontier_ids: set[UUID] = set(nodes_by_id)

    for _hop in range(filters.depth):
        if len(nodes_by_id) >= filters.limit:
            truncated = True
            break
        if not frontier_ids:
            break

        relations = _fetch_neighbors(workspace, frontier_ids)
        next_frontier: set[UUID] = set()
        for rel in relations:
            for entity in (rel.source, rel.target):
                eid = entity.id
                if eid in nodes_by_id:
                    continue
                if len(nodes_by_id) >= filters.limit:
                    truncated = True
                    break
                nodes_by_id[eid] = entity
                next_frontier.add(eid)
            if truncated:
                break
        if truncated:
            break
        frontier_ids = next_frontier

    node_ids = set(nodes_by_id)
    edges: list[dict] = []
    seen_edge_ids: set[UUID] = set()
    if node_ids:
        relations = (
            KnowledgeRelation.objects.filter(document__workspace=workspace)
            .filter(source_id__in=node_ids, target_id__in=node_ids)
            .select_related("source", "target")
            .order_by("created_at", "id")
        )
        for rel in relations:
            if rel.id in seen_edge_ids:
                continue
            seen_edge_ids.add(rel.id)
            edges.append(serialize_edge(rel))

    ordered_nodes = sorted(
        nodes_by_id.values(),
        key=lambda e: (e.created_at, e.id),
    )
    return {
        "workspace": workspace.name,
        "filters": filters.as_response_dict(),
        "truncated": truncated,
        "nodes": [serialize_graph_node(e) for e in ordered_nodes],
        "edges": edges,
    }


def build_filtered_graph_for_workspace_name(
    name: str,
    filters: GraphFilters,
) -> dict:
    workspace = Workspace.objects.get(name=name)
    return build_filtered_workspace_graph(workspace, filters)


def build_filtered_graphs_for_group(
    group_name: str,
    filters: GraphFilters,
) -> list[dict]:
    scope = resolve_group_search_scope(group_name)
    if scope.is_empty:
        return []

    if scope.tag == GroupTag.WORKSPACE:
        workspaces = get_group_workspaces_qs(group_name).order_by("name")
        return [build_filtered_workspace_graph(ws, filters) for ws in workspaces]

    if scope.tag == GroupTag.FILES:
        graphs = []
        doc_ids_by_ws: dict[str, list[UUID]] = {}
        for doc in Document.objects.filter(id__in=scope.document_ids).select_related(
            "workspace"
        ):
            doc_ids_by_ws.setdefault(doc.workspace.name, []).append(doc.id)
        for ws_name, doc_ids in sorted(doc_ids_by_ws.items()):
            workspace = Workspace.objects.get(name=ws_name)
            file_names = list(
                Document.objects.filter(id__in=doc_ids).values_list("file_name", flat=True)
            )
            scoped_filters = GraphFilters(
                entity_types=filters.entity_types,
                file_names=file_names,
                depth=filters.depth,
                limit=filters.limit,
            )
            graphs.append(build_filtered_workspace_graph(workspace, scoped_filters))
        return graphs

    if scope.tag == GroupTag.ENTITY:
        graphs = []
        entity_ids_by_ws: dict[str, list[UUID]] = {}
        for entity in KnowledgeEntity.objects.filter(id__in=scope.entity_ids).select_related(
            "document__workspace"
        ):
            entity_ids_by_ws.setdefault(entity.document.workspace.name, []).append(
                entity.id
            )
        for ws_name, entity_ids in sorted(entity_ids_by_ws.items()):
            workspace = Workspace.objects.get(name=ws_name)
            graphs.append(
                build_graph_from_seed_ids(
                    workspace,
                    entity_ids,
                    depth=filters.depth,
                    limit=filters.limit,
                    entity_types=filters.entity_types,
                    file_names=filters.file_names,
                    filters=filters,
                )
            )
        return graphs

    graphs = []
    relation_ids_by_ws: dict[str, list[UUID]] = {}
    for relation in KnowledgeRelation.objects.filter(
        id__in=scope.relation_ids
    ).select_related("document__workspace"):
        relation_ids_by_ws.setdefault(relation.document.workspace.name, []).append(
            relation.id
        )
    for ws_name, relation_ids in sorted(relation_ids_by_ws.items()):
        workspace = Workspace.objects.get(name=ws_name)
        graphs.append(
            build_graph_from_relation_ids(
                workspace,
                relation_ids,
                depth=filters.depth,
                limit=filters.limit,
                filters=filters,
            )
        )
    return graphs


def build_graph_from_relation_ids(
    workspace: Workspace,
    relation_ids: list[UUID],
    *,
    depth: int,
    limit: int,
    filters: GraphFilters | None = None,
) -> dict:
    if filters is None:
        filters = GraphFilters(
            entity_types=None,
            file_names=None,
            depth=depth,
            limit=limit,
        )
    if not relation_ids:
        return {
            "workspace": workspace.name,
            "filters": filters.as_response_dict(),
            "truncated": False,
            "nodes": [],
            "edges": [],
        }

    relations = list(
        KnowledgeRelation.objects.filter(
            id__in=relation_ids,
            document__workspace=workspace,
        ).select_related("source", "target")
    )
    seed_ids = list(
        {rel.source_id for rel in relations} | {rel.target_id for rel in relations}
    )
    graph = build_graph_from_seed_ids(
        workspace,
        seed_ids,
        depth=depth,
        limit=limit,
        filters=filters,
    )
    member_edges = [serialize_edge(rel) for rel in relations]
    seen = {edge["id"] for edge in member_edges}
    graph["edges"] = member_edges + [
        edge for edge in graph["edges"] if edge["id"] not in seen
    ]
    return graph


# Legacy helpers (delegate to filtered builder with defaults)

def build_workspace_graph(workspace: Workspace) -> dict:
    return build_filtered_workspace_graph(
        workspace,
        GraphFilters(
            entity_types=None,
            file_names=None,
            depth=DEFAULT_GRAPH_DEPTH,
            limit=DEFAULT_GRAPH_LIMIT,
        ),
    )


def build_graph_for_workspace_name(name: str) -> dict:
    return build_filtered_graph_for_workspace_name(
        name,
        GraphFilters(
            entity_types=None,
            file_names=None,
            depth=DEFAULT_GRAPH_DEPTH,
            limit=DEFAULT_GRAPH_LIMIT,
        ),
    )


