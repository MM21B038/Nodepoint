from __future__ import annotations

from typing import Any, Dict, List, cast
import logging
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Condition,
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from nodepoint.settings_loader import quadrant_config

logger = logging.getLogger(__name__)

_cfg: Dict[str, Any] = quadrant_config()
client: QdrantClient = QdrantClient(host=_cfg["host"], port=_cfg["port"])
COLLECTION_NAME: str = _cfg["collection_name"]


def _ensure_collection() -> None:
    if not client.collection_exists(collection_name=COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=_cfg["vector_size"], distance=Distance.COSINE
            ),
        )


def ingest_vector(
    point_id: str | UUID,
    vector: List[float],
    payload: Dict[str, Any],
) -> bool:
    _ensure_collection()
    try:
        point = PointStruct(id=str(point_id), vector=vector, payload=payload)
        client.upsert(collection_name=COLLECTION_NAME, points=[point])
        return True
    except Exception:
        logger.exception("Failed to upsert vector for point %s", point_id)
        return False


def search_vector(
    query_vector: List[float],
    limit: int = 10,
    filter: Filter | None = None,
) -> Any:
    _ensure_collection()
    try:
        return client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=limit,
            query_filter=filter,
        )
    except Exception:
        logger.exception("Vector search failed")
        return None


def _normalize_search_hits(results: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for hit in results:
        payload = hit.payload or {}
        normalized.append(
            {
                "id": str(hit.id),
                "score": hit.score,
                "type": payload.get("type"),
                "entity_type": payload.get("entity_type"),
                "workspace": payload.get("workspace"),
                "payload": payload,
            }
        )
    return normalized


def search_by_workspace(
    query_vector: List[float],
    workspace: str,
    limit: int = 10,
    type_filter: str | None = None,
) -> List[Dict[str, Any]]:
    return search_by_workspaces(
        query_vector, [workspace], limit=limit, type_filter=type_filter
    )


def search_by_workspaces(
    query_vector: List[float],
    workspaces: List[str],
    limit: int = 10,
    type_filter: str | None = None,
) -> List[Dict[str, Any]]:
    if not workspaces:
        return []

    conditions: List[Condition] = [
        FieldCondition(key="workspace", match=MatchAny(any=workspaces)),
    ]
    if type_filter:
        conditions.append(
            FieldCondition(key="type", match=MatchValue(value=type_filter))
        )
    qfilter = Filter(must=cast(List[Condition], conditions))
    results = search_vector(query_vector, limit=limit, filter=qfilter)
    if results is None:
        return []
    return _normalize_search_hits(results)


def set_payload_for_points(
    point_ids: List[Any], payload: Dict[str, Any]
) -> None:
    if not point_ids:
        return
    _ensure_collection()
    try:
        client.set_payload(
            collection_name=COLLECTION_NAME,
            payload=payload,
            points=[str(point_id) for point_id in point_ids],
        )
    except Exception:
        logger.exception("Failed to set payload for %s point(s)", len(point_ids))


def rename_workspace_vectors(workspace_id: int, new_name: str) -> None:
    from nodepoint.models import Document, DocumentChunk, KnowledgeEntity, KnowledgeRelation

    doc_ids = Document.objects.filter(workspace_id=workspace_id).values_list(
        "id", flat=True
    )
    if not doc_ids:
        return

    point_ids: List[Any] = []
    point_ids.extend(
        KnowledgeEntity.objects.filter(document_id__in=doc_ids).values_list(
            "id", flat=True
        )
    )
    point_ids.extend(
        KnowledgeRelation.objects.filter(document_id__in=doc_ids).values_list(
            "id", flat=True
        )
    )
    point_ids.extend(
        DocumentChunk.objects.filter(document_id__in=doc_ids).values_list(
            "id", flat=True
        )
    )
    set_payload_for_points(point_ids, {"workspace": new_name})
