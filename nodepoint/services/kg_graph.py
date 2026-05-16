from __future__ import annotations

from nodepoint.models import KnowledgeEntity, KnowledgeRelation, Workspace


def serialize_entity(entity: KnowledgeEntity) -> dict:
    return {
        "id": str(entity.id),
        "name": entity.name,
        "entity_type": entity.entity_type,
        "attributes": entity.attributes,
        "document_id": str(entity.document_id),
        "file_name": entity.document.file_name,
        "vector": entity.vector,
        "created_at": entity.created_at,
    }


def serialize_edge(relation: KnowledgeRelation) -> dict:
    return {
        "source": relation.source.name,
        "target": relation.target.name,
    }


def build_workspace_graph(workspace: Workspace) -> dict:
    entities = (
        KnowledgeEntity.objects.filter(document__workspace=workspace)
        .select_related("document")
        .order_by("created_at")
    )
    relations = (
        KnowledgeRelation.objects.filter(document__workspace=workspace)
        .select_related("source", "target")
        .order_by("created_at")
    )
    return {
        "workspace": workspace.name,
        "nodes": [serialize_entity(e) for e in entities],
        "edges": [serialize_edge(r) for r in relations],
    }


def build_graph_for_workspace_name(name: str) -> dict:
    workspace = Workspace.objects.get(name=name)
    return build_workspace_graph(workspace)


def build_graphs_for_flagged_workspaces() -> list[dict]:
    workspaces = Workspace.objects.filter(is_flag=True).order_by("name")
    return [build_workspace_graph(ws) for ws in workspaces]
