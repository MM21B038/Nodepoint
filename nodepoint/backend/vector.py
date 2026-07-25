from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List
from uuid import UUID

from nodepoint.agent.agent import Agent
from nodepoint.models import DocumentChunk, KnowledgeEntity, KnowledgeRelation
from nodepoint.quadrant.manager import ingest_vector


@lru_cache(maxsize=1)
def get_agent() -> Agent:
    return Agent()


def create_entity_payload(entity: KnowledgeEntity) -> Dict[str, Any]:
    attributes = ", ".join(
        f"{key}: {value}" for key, value in (entity.attributes or {}).items()
    )
    workspace_name = entity.document.workspace.name
    doc = f"{entity.name} entity of type {entity.entity_type} with attributes {attributes}"
    vector = get_agent().vector(doc).squeeze().tolist()
    return {
        "type": "entity",
        "entity_type": entity.entity_type,
        "workspace": workspace_name,
        "vector": vector,
    }


def create_relation_payload(relation: KnowledgeRelation) -> Dict[str, Any]:
    workspace_name = relation.document.workspace.name
    doc = (
        f"{relation.source.name} {relation.type_description} {relation.target.name}. "
        f"{relation.description}"
    )
    vector = get_agent().vector(doc).squeeze().tolist()
    return {
        "type": "relation",
        "workspace": workspace_name,
        "vector": vector,
    }


def ingest_entity_vector(point_id: UUID | str, entity: KnowledgeEntity) -> bool:
    payload = create_entity_payload(entity)
    vector = payload.pop("vector")
    return ingest_vector(str(point_id), vector, payload)


def ingest_relation_vector(point_id: UUID | str, relation: KnowledgeRelation) -> bool:
    payload = create_relation_payload(relation)
    vector = payload.pop("vector")
    return ingest_vector(str(point_id), vector, payload)


def create_chunk_payload(chunk: DocumentChunk) -> Dict[str, Any]:
    from nodepoint.mongo.manager import get_chunk_text

    workspace_name = chunk.document.workspace.name
    text = get_chunk_text(chunk.id) or ""
    snippet = text[:2000] if len(text) > 2000 else text
    doc = f"Document chunk {chunk.index} from {chunk.document.file_name}: {snippet}"
    vector = get_agent().vector(doc).squeeze().tolist()
    return {
        "type": "chunk",
        "workspace": workspace_name,
        "file_name": chunk.document.file_name,
        "chunk_index": chunk.index,
        "document_id": str(chunk.document_id),
        "vector": vector,
    }


def ingest_chunk_vector(point_id: UUID | str, chunk: DocumentChunk) -> bool:
    payload = create_chunk_payload(chunk)
    vector = payload.pop("vector")
    return ingest_vector(str(point_id), vector, payload)
