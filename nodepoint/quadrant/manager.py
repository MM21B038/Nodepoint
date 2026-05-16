import logging

from qdrant_client import QdrantClient
from qdrant_client.models import (
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

_cfg = quadrant_config()
client = QdrantClient(host=_cfg["host"], port=_cfg["port"])
COLLECTION_NAME = _cfg["collection_name"]


def _ensure_collection():
    if not client.collection_exists(collection_name=COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=_cfg["vector_size"], distance=Distance.COSINE
            ),
        )


def ingest_vector(point_id, vector, payload):
    _ensure_collection()
    try:
        point = PointStruct(id=str(point_id), vector=vector, payload=payload)
        client.upsert(collection_name=COLLECTION_NAME, points=[point])
        return True
    except Exception:
        logger.exception("Failed to upsert vector for point %s", point_id)
        return False


def search_vector(query_vector, limit=10, filter=None):
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


def _normalize_search_hits(results) -> list[dict]:
    normalized = []
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
    query_vector,
    workspace: str,
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    return search_by_workspaces(
        query_vector, [workspace], limit=limit, type_filter=type_filter
    )


def search_by_workspaces(
    query_vector,
    workspaces: list[str],
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    if not workspaces:
        return []

    conditions = [
        FieldCondition(key="workspace", match=MatchAny(any=workspaces)),
    ]
    if type_filter:
        conditions.append(
            FieldCondition(key="type", match=MatchValue(value=type_filter))
        )
    qfilter = Filter(must=conditions)
    results = search_vector(query_vector, limit=limit, filter=qfilter)
    if results is None:
        return []
    return _normalize_search_hits(results)
