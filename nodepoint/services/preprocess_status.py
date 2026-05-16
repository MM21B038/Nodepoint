from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import Count

from nodepoint.enums import Status
from nodepoint.models import KnowledgeEntity, KnowledgeRelation, Workspace

VECTOR_BUCKETS = ("pending", "completed", "failed")


def _empty_vector_counts() -> dict[str, int]:
    return {k: 0 for k in VECTOR_BUCKETS} | {"total": 0}


def _vector_bucket(status: str) -> str:
    if status == Status.COMPLETED:
        return "completed"
    if status == Status.FAILED:
        return "failed"
    return "pending"


def _aggregate_vectors(qs) -> dict[str, dict[str, int]]:
    """Return {document_id: {total, pending, completed, failed}}."""
    by_doc: dict[Any, dict[str, int]] = defaultdict(_empty_vector_counts)
    rows = qs.values("document_id", "vector").annotate(count=Count("id"))
    for row in rows:
        doc_id = row["document_id"]
        bucket = _vector_bucket(row["vector"])
        count = row["count"]
        by_doc[doc_id]["total"] += count
        by_doc[doc_id][bucket] += count
    return dict(by_doc)


def _workspace_vector_totals(workspace: Workspace) -> dict[str, dict[str, int]]:
    entities = KnowledgeEntity.objects.filter(document__workspace=workspace)
    relations = KnowledgeRelation.objects.filter(document__workspace=workspace)
    entity_rows = entities.values("vector").annotate(count=Count("id"))
    relation_rows = relations.values("vector").annotate(count=Count("id"))

    def _from_rows(rows):
        out = _empty_vector_counts()
        for row in rows:
            bucket = _vector_bucket(row["vector"])
            out[bucket] += row["count"]
            out["total"] += row["count"]
        return out

    return {
        "entities": _from_rows(entity_rows),
        "relations": _from_rows(relation_rows),
    }


def _embedding_progress(
    entities: dict[str, int], relations: dict[str, int]
) -> float:
    total = entities.get("total", 0) + relations.get("total", 0)
    if total == 0:
        return 1.0
    done = entities.get("completed", 0) + relations.get("completed", 0)
    return round(done / total, 4)


def derive_file_phase(
    document_status: str,
    entities: dict[str, int],
    relations: dict[str, int],
) -> str:
    if document_status in (Status.INVALID, Status.TERMINATED):
        return "failed"
    if document_status in (Status.PENDING, Status.QUEUED):
        return "queued"
    if document_status == Status.INPROGRESS:
        return "processing"
    if document_status == Status.FAILED:
        return "failed"
    if document_status != Status.COMPLETED:
        return "failed"

    kg_total = entities.get("total", 0) + relations.get("total", 0)
    if kg_total == 0:
        return "kg_ready"

    pending = entities.get("pending", 0) + relations.get("pending", 0)
    failed = entities.get("failed", 0) + relations.get("failed", 0)
    if pending > 0:
        return "embedding"
    if failed > 0 and entities.get("completed", 0) + relations.get("completed", 0) == 0:
        return "failed"
    if failed > 0:
        return "embedding"
    return "ready"


def overall_from_files(file_phases: list[str], documents_total: int) -> dict[str, Any]:
    if documents_total == 0:
        return {
            "phase": "idle",
            "ready": True,
            "documents_total": 0,
            "documents_failed": 0,
        }

    documents_failed = sum(1 for p in file_phases if p == "failed")
    ready = all(p == "ready" for p in file_phases) and documents_failed == 0

    if any(p == "processing" for p in file_phases):
        phase = "processing"
    elif any(p == "queued" for p in file_phases):
        phase = "queued"
    elif any(p == "embedding" for p in file_phases):
        phase = "embedding"
    elif documents_failed > 0 and not ready:
        phase = "failed"
    elif any(p == "kg_ready" for p in file_phases):
        phase = "kg_ready"
    elif ready:
        phase = "ready"
    else:
        phase = "embedding"

    return {
        "phase": phase,
        "ready": ready,
        "documents_total": documents_total,
        "documents_failed": documents_failed,
    }


def _documents_by_status(docs) -> dict[str, int]:
    counts = {s.value: 0 for s in Status}
    for doc in docs:
        counts[doc.status] = counts.get(doc.status, 0) + 1
    return counts


def build_workspace_preprocess_status(workspace: Workspace) -> dict[str, Any]:
    docs = list(workspace.documents.order_by("-created_at"))
    entity_by_doc = _aggregate_vectors(
        KnowledgeEntity.objects.filter(document__workspace=workspace)
    )
    relation_by_doc = _aggregate_vectors(
        KnowledgeRelation.objects.filter(document__workspace=workspace)
    )

    files: list[dict[str, Any]] = []
    file_phases: list[str] = []

    for doc in docs:
        entities = entity_by_doc.get(doc.id, _empty_vector_counts())
        relations = relation_by_doc.get(doc.id, _empty_vector_counts())
        phase = derive_file_phase(doc.status, entities, relations)
        file_phases.append(phase)
        files.append(
            {
                "id": str(doc.id),
                "file_name": doc.file_name,
                "document_status": doc.status,
                "content": doc.content,
                "phase": phase,
                "uploaded_at": doc.created_at,
                "entities": entities,
                "relations": relations,
                "embedding_progress": _embedding_progress(entities, relations),
            }
        )

    vectors = _workspace_vector_totals(workspace)
    overall = overall_from_files(file_phases, len(docs))

    return {
        "workspace": workspace.name,
        "overall": overall,
        "documents": {"by_status": _documents_by_status(docs)},
        "vectors": vectors,
        "files": files,
    }
