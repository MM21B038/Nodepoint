from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

import django_rq
from django.conf import settings
from django_rq import get_connection
from rq import Retry

from nodepoint.backend.content_extractor import read_document_content
from nodepoint.backend.kg_builder import split_doc
from nodepoint.enums import Status
from nodepoint.models import Document, DocumentChunk, Workspace
from nodepoint.mongo.manager import delete_chunks_for_document, ingest_chunk
from nodepoint.services.chunking import (
    enqueue_chunks_for_documents,
    prepare_document,
    run_chunk_preprocess_failed_batch,
    run_prepare_failed_documents_batch,
    run_prepare_legacy_batch,
)
from nodepoint.services.preprocess_status import workspace_needs_preprocess
from nodepoint.services.vector import vector_preprocess

logger = logging.getLogger(__name__)

_PIPELINE_LOCK_TTL = int(os.getenv("PREPROCESS_PIPELINE_LOCK_TTL", "14400"))


def _orchestrator_timeout() -> str:
    return os.getenv(
        "PREPROCESS_JOB_TIMEOUT",
        getattr(settings, "PREPROCESS_JOB_TIMEOUT", "3h"),
    )


def _orchestrator_queue_name(priority: bool = False, background: bool = False) -> str:
    if priority:
        return getattr(settings, "RQ_QUEUE_ORCHESTRATOR_HIGH", "high")
    if background:
        return getattr(settings, "RQ_QUEUE_ORCHESTRATOR_LOW", "low")
    return getattr(settings, "RQ_QUEUE_ORCHESTRATOR", "orchestrator")


def _orchestrator_queue(queue_name: str | None = None):
    name = queue_name or getattr(settings, "RQ_QUEUE_ORCHESTRATOR", "orchestrator")
    return django_rq.get_queue(name)


_RETRY = Retry(max=3, interval=[10, 30, 60])


def _pipeline_lock_key(workspace_name: str) -> str:
    return f"nodepoint:preprocess:pipeline:{workspace_name}"


def try_acquire_workspace_pipeline_lock(workspace_name: str | None) -> bool:
    if not workspace_name:
        return True
    conn = get_connection()
    return bool(
        conn.set(_pipeline_lock_key(workspace_name), "1", nx=True, ex=_PIPELINE_LOCK_TTL)
    )


def release_workspace_pipeline_lock(workspace_name: str | None) -> None:
    if not workspace_name:
        return
    get_connection().delete(_pipeline_lock_key(workspace_name))


def run_prepare_document(doc_id: UUID) -> None:
    """Job 1: split file into chunks (Postgres + Mongo) for uploaded document."""
    try:
        doc = Document.objects.get(id=doc_id)
    except Document.DoesNotExist:
        logger.error("run_prepare_document: document %s not found", doc_id)
        return

    if not doc.file:
        Document.objects.filter(id=doc_id).update(status=Status.INVALID)
        return

    filepath = doc.file.path
    if not os.path.exists(filepath):
        Document.objects.filter(id=doc_id).update(status=Status.INVALID)
        return

    prepare_document(doc_id, filepath)


def run_chunk_preprocess_batch(
    workspace_name: str | None = None,
    document_ids: list[UUID] | None = None,
) -> int:
    """Enqueue process_chunk jobs without blocking the orchestrator worker."""
    from nodepoint.services.preprocess_recovery import recover_orphaned_chunks

    recover_orphaned_chunks(workspace=workspace_name)
    count = enqueue_chunks_for_documents(
        document_ids=document_ids,
        workspace_name=workspace_name,
        wait=False,
    )
    logger.info(
        "run_chunk_preprocess_batch: enqueued %s chunk job(s) "
        "(workspace=%s, document_ids=%s)",
        count,
        workspace_name,
        document_ids,
    )
    return count


def run_vector_preprocess_batch(workspace_name: str | None = None) -> None:
    """Catch-up sweep: enqueue embeddings for any remaining PENDING/FAILED vectors."""
    vector_preprocess(workspace_name=workspace_name)
    logger.info(
        "run_vector_preprocess_batch: vector sweep enqueued (workspace=%s)",
        workspace_name,
    )


def run_chunk_mongo_repair_batch(workspace_name: str | None = None) -> int:
    """Rebuild Mongo chunk rows for documents with content=false or missing chunk text."""
    try:
        repaired = 0
        doc_qs = Document.objects.filter(content=False)
        if workspace_name:
            doc_qs = doc_qs.filter(workspace__name=workspace_name)
        doc_ids = set(doc_qs.values_list("id", flat=True))

        content_qs = Document.objects.filter(content=True)
        if workspace_name:
            content_qs = content_qs.filter(workspace__name=workspace_name)

        for doc in content_qs.prefetch_related("chunks"):
            for chunk in doc.chunks.all():
                from nodepoint.mongo.manager import get_chunk_text

                if get_chunk_text(chunk.id) is None:
                    doc_ids.add(doc.id)
                    break

        for doc in Document.objects.filter(id__in=doc_ids).select_related("workspace"):
            if not doc.file or not os.path.exists(doc.file.path):
                continue

            content = read_document_content(doc.file.path)
            if content is None or not content.strip():
                Document.objects.filter(id=doc.id).update(content=False, status=Status.FAILED)
                continue

            text_chunks = split_doc(content)
            if not text_chunks:
                continue

            delete_chunks_for_document(doc.id)
            DocumentChunk.objects.filter(document_id=doc.id).delete()

            ok = True
            for index, text in enumerate(text_chunks):
                chunk = DocumentChunk.objects.create(
                    document=doc,
                    index=index,
                    status=Status.PENDING,
                    vector=Status.PENDING,
                )
                if not ingest_chunk(chunk.id, doc.id, index, text):
                    ok = False
                    chunk.delete()
                    break

            if ok:
                Document.objects.filter(id=doc.id).update(content=True, status=Status.QUEUED)
                repaired += 1
            else:
                Document.objects.filter(id=doc.id).update(content=False, status=Status.FAILED)

        logger.info(
            "run_chunk_mongo_repair_batch: repaired %s document(s) (workspace=%s)",
            repaired,
            workspace_name,
        )
        return repaired
    finally:
        release_workspace_pipeline_lock(workspace_name)


def schedule_workspace_pipeline_tail(workspace_name: str | None) -> dict[str, Any]:
    """
    Coalesced workspace tail: vector catch-up sweep then mongo repair.

    Only one tail runs per workspace at a time (Redis lock). Upload paths call this
    after document-scoped chunk enqueue; workspace POST acquires the lock up front.
    """
    if not workspace_name:
        return {"coalesced": False, "skipped": True}

    if not try_acquire_workspace_pipeline_lock(workspace_name):
        logger.info(
            "schedule_workspace_pipeline_tail: coalesced for workspace=%s",
            workspace_name,
        )
        return {"coalesced": True, "workspace_name": workspace_name}

    queue = _orchestrator_queue()
    job_kwargs = {"job_timeout": _orchestrator_timeout(), "retry": _RETRY}

    j3 = queue.enqueue(run_vector_preprocess_batch, workspace_name, **job_kwargs)
    j4 = queue.enqueue(
        run_chunk_mongo_repair_batch,
        workspace_name,
        depends_on=j3,
        **job_kwargs,
    )
    logger.info(
        "schedule_workspace_pipeline_tail: queued vector+repair for workspace=%s",
        workspace_name,
    )
    return {
        "coalesced": False,
        "workspace_name": workspace_name,
        "vector_preprocess": j3.id,
        "chunk_mongo_repair": j4.id,
    }


def run_upload_failed_catchup_batch(exclude_document_id: UUID | None = None) -> dict[str, int]:
    """
    Retry failed preprocess work in every workspace (used after upload).

    Re-prepares documents that failed before chunking and re-enqueues failed chunks.
    The new upload is excluded so its document-scoped chunk step owns that work.
    """
    exclude = [exclude_document_id] if exclude_document_id else None
    prepared = run_prepare_failed_documents_batch(exclude_document_ids=exclude)
    chunks = run_chunk_preprocess_failed_batch(exclude_document_ids=exclude)
    logger.info(
        "run_upload_failed_catchup_batch: prepared=%s chunks_enqueued=%s",
        prepared,
        chunks,
    )
    return {"prepared": prepared, "chunks_enqueued": chunks}


def _enqueue_upload_preprocess(
    uploaded_document_id: UUID,
    workspace_name: str | None,
) -> dict[str, Any]:
    queue = _orchestrator_queue()
    job_kwargs = {"job_timeout": _orchestrator_timeout(), "retry": _RETRY}

    j1 = queue.enqueue(run_prepare_document, uploaded_document_id, **job_kwargs)
    j2 = queue.enqueue(
        run_chunk_preprocess_batch,
        workspace_name,
        document_ids=[uploaded_document_id],
        depends_on=[j1],
        **job_kwargs,
    )
    j2b = queue.enqueue(
        run_upload_failed_catchup_batch,
        uploaded_document_id,
        depends_on=[j1],
        **job_kwargs,
    )
    j3 = queue.enqueue(
        schedule_workspace_pipeline_tail,
        workspace_name,
        depends_on=[j2, j2b],
        **job_kwargs,
    )

    jobs = {
        "prepare_document": j1.id,
        "chunk_preprocess": j2.id,
        "failed_catchup": j2b.id,
        "workspace_tail": j3.id,
    }
    message = (
        "Preprocess pipeline queued: prepare document → chunk KG (document-scoped) "
        "→ failed catch-up (all workspaces) → coalesced workspace embeddings/repair"
    )
    logger.info("%s: %s", message, jobs)
    return {
        "message": message,
        "steps": [
            "prepare_document",
            "chunk_preprocess",
            "failed_catchup",
            "workspace_tail",
        ],
        "jobs": jobs,
    }


def _enqueue_workspace_preprocess(
    workspace_name: str | None,
    *,
    orchestrator_queue_name: str | None = None,
) -> dict[str, Any]:
    if workspace_name and not try_acquire_workspace_pipeline_lock(workspace_name):
        message = f"Preprocess pipeline already queued (workspace={workspace_name})"
        logger.info(message)
        return {
            "message": message,
            "coalesced": True,
            "steps": [],
            "jobs": {},
        }

    queue = _orchestrator_queue(orchestrator_queue_name)
    job_kwargs = {"job_timeout": _orchestrator_timeout(), "retry": _RETRY}

    j1 = queue.enqueue(run_prepare_legacy_batch, workspace_name, **job_kwargs)
    j2 = queue.enqueue(
        run_chunk_preprocess_batch,
        workspace_name,
        depends_on=[j1],
        **job_kwargs,
    )
    j3 = queue.enqueue(
        run_vector_preprocess_batch,
        workspace_name,
        depends_on=[j2],
        **job_kwargs,
    )
    j4 = queue.enqueue(
        run_chunk_mongo_repair_batch,
        workspace_name,
        depends_on=[j3],
        **job_kwargs,
    )

    jobs = {
        "prepare_legacy": j1.id,
        "chunk_preprocess": j2.id,
        "vector_preprocess": j3.id,
        "chunk_mongo_repair": j4.id,
    }
    message = (
        "Preprocess pipeline queued: prepare legacy → chunk KG (parallel) "
        "→ embeddings catch-up → mongo repair"
    )
    if workspace_name:
        message += f" (workspace={workspace_name})"

    logger.info("%s: %s", message, jobs)
    return {
        "message": message,
        "steps": [
            "prepare_legacy",
            "chunk_preprocess",
            "vector_preprocess",
            "chunk_mongo_repair",
        ],
        "jobs": jobs,
    }


def enqueue_preprocess_pipeline(
    uploaded_document_id: UUID | None = None,
    workspace_name: str | None = None,
    *,
    orchestrator_queue_name: str | None = None,
) -> dict[str, Any]:
    if uploaded_document_id is not None:
        return _enqueue_upload_preprocess(uploaded_document_id, workspace_name)
    return _enqueue_workspace_preprocess(
        workspace_name,
        orchestrator_queue_name=orchestrator_queue_name,
    )


def _other_workspaces_needing_preprocess(exclude: str) -> list[str]:
    names: list[str] = []
    for ws in Workspace.objects.order_by("name"):
        if ws.name == exclude:
            continue
        if workspace_needs_preprocess(ws):
            names.append(ws.name)
    return names


def enqueue_priority_workspace_preprocess(
    workspace_name: str,
    *,
    priority: bool = False,
    include_other_workspaces: bool = False,
) -> dict[str, Any]:
    """
    POST preprocess: prioritize one workspace and optionally queue the rest.

    Priority workspace uses the high orchestrator queue when priority=True;
    other incomplete workspaces use the low queue (or orchestrator when priority=False).
    """
    priority_queue = _orchestrator_queue_name(priority=priority)
    background_queue = _orchestrator_queue_name(background=priority)

    priority_pipeline = _enqueue_workspace_preprocess(
        workspace_name,
        orchestrator_queue_name=priority_queue,
    )

    other_workspaces: list[dict[str, Any]] = []
    if include_other_workspaces:
        for other_name in _other_workspaces_needing_preprocess(workspace_name):
            result = _enqueue_workspace_preprocess(
                other_name,
                orchestrator_queue_name=background_queue,
            )
            entry: dict[str, Any] = {
                "workspace": other_name,
                "queued": not result.get("coalesced"),
                "coalesced": bool(result.get("coalesced")),
                "skipped_reason": None,
            }
            if result.get("coalesced"):
                entry["skipped_reason"] = "pipeline_already_queued"
            other_workspaces.append(entry)

    message = priority_pipeline.get("message") or (
        f"Preprocessing queued for workspace '{workspace_name}'"
    )
    if include_other_workspaces and other_workspaces:
        queued_count = sum(1 for o in other_workspaces if o["queued"])
        message += (
            f"; {queued_count} other workspace(s) queued for background preprocess"
        )

    return {
        "message": message,
        "priority_workspace": workspace_name,
        "priority_pipeline": priority_pipeline,
        "other_workspaces": other_workspaces,
    }
