from __future__ import annotations

import logging
import os
from typing import Any

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


def _recovery_enabled() -> bool:
    raw = os.getenv("PREPROCESS_RECOVERY_ON_STARTUP", "1")
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def reset_orphaned_chunk_statuses() -> dict[str, int]:
    """Move QUEUED/INPROGRESS chunks back to PENDING after RQ job loss."""
    in_progress_reset = DocumentChunk.objects.filter(status=Status.INPROGRESS).update(
        status=Status.PENDING
    )
    queued_reset = DocumentChunk.objects.filter(status=Status.QUEUED).update(
        status=Status.PENDING
    )
    return {
        "in_progress_reset": in_progress_reset,
        "queued_reset": queued_reset,
    }


def _rollup_stale_document_statuses() -> int:
    """Reconcile document rows left INPROGRESS/QUEUED after chunk status reset."""
    doc_ids = list(
        Document.objects.filter(
            status__in=[Status.INPROGRESS, Status.QUEUED],
        ).values_list("id", flat=True)
    )
    for doc_id in doc_ids:
        rollup_document_status(doc_id)
    return len(doc_ids)


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
    if not force and not conn.set(RECOVERY_LOCK_KEY, "1", nx=True, ex=_RECOVERY_LOCK_TTL):
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
