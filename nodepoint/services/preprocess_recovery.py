from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from django_rq import get_connection

from nodepoint.enums import Status
from nodepoint.models import Document, DocumentChunk
from nodepoint.services.chunking import (
    enqueue_chunks_for_documents,
    rollup_document_status,
    run_prepare_failed_documents_batch,
    run_prepare_legacy_batch,
)
from nodepoint.services.vector import vector_preprocess

logger = logging.getLogger(__name__)

RECOVERY_LOCK_KEY = "nodepoint:preprocess:recovery:startup"
_RECOVERY_LOCK_TTL = int(os.getenv("PREPROCESS_RECOVERY_LOCK_TTL", "300"))


def _live_chunk_job_ids() -> set[UUID]:
    from nodepoint.services.queue_status import _chunk_ids_with_live_rq_jobs

    return _chunk_ids_with_live_rq_jobs()


def has_orphaned_preprocess_work(*, workspace: str | None = None) -> bool:
    """True when Postgres has in-flight chunks with no live process_chunk RQ job."""
    live = _live_chunk_job_ids()
    qs = DocumentChunk.objects.filter(status__in=[Status.QUEUED, Status.INPROGRESS])
    if workspace:
        qs = qs.filter(document__workspace__name=workspace)
    return qs.exclude(id__in=live).exists()


def _recovery_enabled() -> bool:
    raw = os.getenv("PREPROCESS_RECOVERY_ON_STARTUP", "1")
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def reset_orphaned_chunk_statuses(*, workspace: str | None = None) -> dict[str, int]:
    """Move orphaned QUEUED/INPROGRESS chunks back to PENDING after RQ job loss."""
    live = _live_chunk_job_ids()
    in_progress_qs = DocumentChunk.objects.filter(status=Status.INPROGRESS).exclude(
        id__in=live
    )
    queued_qs = DocumentChunk.objects.filter(status=Status.QUEUED).exclude(id__in=live)
    if workspace:
        in_progress_qs = in_progress_qs.filter(document__workspace__name=workspace)
        queued_qs = queued_qs.filter(document__workspace__name=workspace)
    in_progress_reset = in_progress_qs.update(status=Status.PENDING)
    queued_reset = queued_qs.update(status=Status.PENDING)
    return {
        "in_progress_reset": in_progress_reset,
        "queued_reset": queued_reset,
    }


def _rollup_stale_document_statuses(*, workspace: str | None = None) -> int:
    """Reconcile document rows left INPROGRESS/QUEUED after chunk status reset."""
    doc_qs = Document.objects.filter(status__in=[Status.INPROGRESS, Status.QUEUED])
    if workspace:
        doc_qs = doc_qs.filter(workspace__name=workspace)
    doc_ids = list(doc_qs.values_list("id", flat=True))
    for doc_id in doc_ids:
        rollup_document_status(doc_id)
    return len(doc_ids)


def recover_orphaned_chunks(*, workspace: str | None = None) -> dict[str, int]:
    """Reset orphaned chunk rows (per workspace or globally) then rollup documents."""
    live = _live_chunk_job_ids()
    stuck_qs = DocumentChunk.objects.filter(
        status__in=[Status.QUEUED, Status.INPROGRESS],
    ).exclude(id__in=live)
    if workspace:
        stuck_qs = stuck_qs.filter(document__workspace__name=workspace)
    if not stuck_qs.exists():
        return {
            "in_progress_reset": 0,
            "queued_reset": 0,
            "documents_rolled_up": 0,
        }
    stats = reset_orphaned_chunk_statuses(workspace=workspace)
    stats["documents_rolled_up"] = _rollup_stale_document_statuses(workspace=workspace)
    logger.info("recover_orphaned_chunks: %s (workspace=%s)", stats, workspace)
    return stats


def run_preprocess_recovery() -> dict[str, Any]:
    """
    Reset orphaned chunk statuses and re-enqueue global chunk + vector backlog.

    Safe after a full restart when Redis queues are empty but Postgres still
    shows QUEUED/INPROGRESS rows.
    """
    stats: dict[str, Any] = {}
    stats.update(reset_orphaned_chunk_statuses())
    stats["documents_rolled_up"] = _rollup_stale_document_statuses()
    stats["legacy_prepared"] = run_prepare_legacy_batch(workspace_name=None)
    stats["failed_prepared"] = run_prepare_failed_documents_batch()
    stats["chunks_enqueued"] = enqueue_chunks_for_documents()
    vector_preprocess()
    stats["vector_sweep"] = True
    logger.info("run_preprocess_recovery: %s", stats)
    return stats


def maybe_run_startup_recovery(*, force: bool = False) -> dict[str, Any] | None:
    """
    Run recovery once per cluster boot (Redis SET NX lock).

    Returns recovery stats when run, None when skipped (disabled, lock held, or
    not forced). Exceptions are logged but not raised so workers still start.
    """
    if not force and not _recovery_enabled():
        logger.info("maybe_run_startup_recovery: disabled via PREPROCESS_RECOVERY_ON_STARTUP")
        return None

    conn = get_connection()
    orphaned = has_orphaned_preprocess_work()

    if force or orphaned:
        conn.delete(RECOVERY_LOCK_KEY)
        conn.set(RECOVERY_LOCK_KEY, "1", ex=_RECOVERY_LOCK_TTL)
        if orphaned:
            logger.info(
                "maybe_run_startup_recovery: recovering stale preprocess work "
                "(orphaned_chunks=%s)",
                _orphaned_chunk_count_global(),
            )
    elif not conn.set(RECOVERY_LOCK_KEY, "1", nx=True, ex=_RECOVERY_LOCK_TTL):
        logger.info("maybe_run_startup_recovery: skipped (recovery lock held)")
        return None

    try:
        stats = run_preprocess_recovery()
        logger.info("maybe_run_startup_recovery: completed %s", stats)
        return stats
    except Exception:
        logger.exception("maybe_run_startup_recovery: recovery failed")
        if force:
            raise
        return None


def _orphaned_chunk_count_global() -> int:
    from nodepoint.services.queue_status import _orphaned_chunk_count

    return _orphaned_chunk_count()
