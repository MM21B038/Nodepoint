"""
Operational snapshot of RQ queues, workers, Redis pipeline locks, and DB backlog.

Response shape is stable for GET /api/preprocess/queue-status/ — see docs/API.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import django_rq
from django.conf import settings
from django.db.models import Count
from django_rq import get_connection
from rq import Worker
from rq.job import Job
from rq.registry import DeferredJobRegistry, FailedJobRegistry, StartedJobRegistry

from nodepoint.enums import Status
from nodepoint.models import (
    Document,
    DocumentChunk,
    KnowledgeEntity,
    KnowledgeRelation,
    Workspace,
)
from nodepoint.services.preprocess_status import build_workspace_preprocess_status

logger = logging.getLogger(__name__)

PIPELINE_LOCK_SCAN_PREFIX = "nodepoint:preprocess:pipeline:*"
MONITORED_QUEUES = ("high", "orchestrator", "low", "chunk", "vector", "default")
MAX_JOBS_LISTED = 40
MAX_FAILED_SAMPLE = 15
MAX_INCOMPLETE_WORKSPACES = 100
ERROR_TRUNCATE = 500

_WORKSPACE_FIRST_ARG_FUNCS = frozenset(
    {
        "run_chunk_preprocess_batch",
        "run_vector_preprocess_batch",
        "run_chunk_mongo_repair_batch",
        "schedule_workspace_pipeline_tail",
        "run_prepare_legacy_batch",
    }
)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _truncate(text: str | None, limit: int = ERROR_TRUNCATE) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _func_short_name(func_name: str | None) -> str | None:
    if not func_name:
        return None
    return func_name.rsplit(".", 1)[-1]


def _parse_args_summary(func_name: str | None, args: tuple, kwargs: dict) -> dict[str, Any]:
    short = _func_short_name(func_name) or ""
    summary: dict[str, Any] = {}

    if short in _WORKSPACE_FIRST_ARG_FUNCS and args and isinstance(args[0], str):
        summary["workspace"] = args[0]
    elif short == "run_prepare_document" and args:
        summary["document_id"] = str(args[0])
    elif short == "process_chunk" and args:
        summary["chunk_id"] = str(args[0])
    elif short == "process_vector" and args:
        summary["point_id"] = str(args[0])
        if len(args) > 1:
            summary["vector_kind"] = args[1]

    if "workspace_name" in kwargs and kwargs["workspace_name"]:
        summary["workspace"] = kwargs["workspace_name"]
    if "document_id" in kwargs and kwargs["document_id"] is not None:
        summary["document_id"] = str(kwargs["document_id"])
    if "document_ids" in kwargs and kwargs["document_ids"]:
        summary["document_ids"] = [str(x) for x in kwargs["document_ids"][:5]]
        if len(kwargs["document_ids"]) > 5:
            summary["document_ids_truncated"] = len(kwargs["document_ids"])

    return summary


def _job_matches_workspace(job: Job, workspace: str) -> bool:
    summary = _parse_args_summary(job.func_name, job.args or (), job.kwargs or {})
    if summary.get("workspace") == workspace:
        return True
    if workspace in str(job.args) or workspace in str(job.kwargs):
        return True
    return False


def _serialize_job(job: Job, *, origin_queue: str | None = None) -> dict[str, Any]:
    origin = origin_queue or getattr(job, "origin", None) or job.origin
    return {
        "id": job.id,
        "function": _func_short_name(job.func_name),
        "status": job.get_status(),
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "ended_at": _iso(job.ended_at),
        "origin_queue": origin,
        "args_summary": _parse_args_summary(
            job.func_name, job.args or (), job.kwargs or {}
        ),
    }


def _fetch_jobs(job_ids: list[str], connection) -> list[Job]:
    jobs: list[Job] = []
    for job_id in job_ids:
        try:
            jobs.append(Job.fetch(job_id, connection=connection))
        except Exception:
            logger.debug("queue_status: could not fetch job %s", job_id, exc_info=True)
    return jobs


def _queue_counts(queue_name: str, connection) -> dict[str, int]:
    queue = django_rq.get_queue(queue_name)
    started = StartedJobRegistry(queue_name, connection=connection)
    failed = FailedJobRegistry(queue_name, connection=connection)
    deferred = DeferredJobRegistry(queue_name, connection=connection)
    return {
        "queued": queue.count,
        "started": started.count,
        "failed": failed.count,
        "deferred": deferred.count,
    }


def _queue_jobs(queue_name: str, connection, *, workspace: str | None) -> list[dict[str, Any]]:
    queue = django_rq.get_queue(queue_name)
    seen: set[str] = set()
    serialized: list[dict[str, Any]] = []

    def add(job: Job, origin: str | None = None) -> None:
        if job.id in seen:
            return
        if workspace and not _job_matches_workspace(job, workspace):
            return
        seen.add(job.id)
        serialized.append(_serialize_job(job, origin_queue=origin or queue_name))

    for job in queue.get_jobs()[:MAX_JOBS_LISTED]:
        add(job)

    started_reg = StartedJobRegistry(queue_name, connection=connection)
    for job_id in started_reg.get_job_ids()[:MAX_JOBS_LISTED]:
        for job in _fetch_jobs([job_id], connection):
            add(job)

    deferred_reg = DeferredJobRegistry(queue_name, connection=connection)
    for job_id in deferred_reg.get_job_ids()[:MAX_JOBS_LISTED]:
        for job in _fetch_jobs([job_id], connection):
            add(job)

    return serialized[:MAX_JOBS_LISTED]


def _failed_sample(queue_name: str, connection) -> list[dict[str, Any]]:
    registry = FailedJobRegistry(queue_name, connection=connection)
    sample: list[dict[str, Any]] = []
    for job_id in registry.get_job_ids()[:MAX_FAILED_SAMPLE]:
        for job in _fetch_jobs([job_id], connection):
            entry = _serialize_job(job, origin_queue=queue_name)
            entry["error"] = _truncate(job.exc_info or str(job.latest_result() or ""))
            sample.append(entry)
    return sample


def build_rq_snapshot(*, workspace: str | None = None) -> dict[str, Any]:
    connection = get_connection()
    queues: dict[str, Any] = {}
    for name in MONITORED_QUEUES:
        if name not in settings.RQ_QUEUES:
            continue
        try:
            queues[name] = {
                "counts": _queue_counts(name, connection),
                "jobs": _queue_jobs(name, connection, workspace=workspace),
                "failed_sample": _failed_sample(name, connection),
            }
        except Exception:
            logger.exception("queue_status: failed to read queue %s", name)
            queues[name] = {"error": f"Could not read queue '{name}'"}

    workers: list[dict[str, Any]] = []
    try:
        for worker in Worker.all(connection=connection):
            workers.append(
                {
                    "name": worker.name,
                    "state": worker.get_state(),
                    "queues": list(worker.queue_names()),
                    "current_job_id": worker.get_current_job_id(),
                    "birth_date": _iso(worker.birth_date),
                    "last_heartbeat": _iso(worker.last_heartbeat),
                }
            )
    except Exception:
        logger.exception("queue_status: failed to list workers")

    return {"queues": queues, "workers": workers}


def _scan_pipeline_locks() -> list[dict[str, Any]]:
    conn = get_connection()
    locks: list[dict[str, Any]] = []
    for key in conn.scan_iter(match=PIPELINE_LOCK_SCAN_PREFIX):
        key_str = key.decode() if isinstance(key, bytes) else str(key)
        workspace = key_str.rsplit(":", 1)[-1]
        ttl = conn.ttl(key)
        locks.append(
            {
                "workspace": workspace,
                "key": key_str,
                "ttl_seconds": ttl if ttl is not None and ttl >= 0 else None,
            }
        )
    locks.sort(key=lambda x: x["workspace"])
    return locks


def _status_counts(qs, field: str = "status") -> dict[str, int]:
    rows = qs.values(field).annotate(count=Count("id"))
    out: dict[str, int] = {}
    total = 0
    for row in rows:
        status = row[field]
        count = row["count"]
        out[status] = count
        total += count
    out["total"] = total
    return out


def _vector_backlog_counts(qs) -> dict[str, int]:
    rows = qs.values("vector").annotate(count=Count("id"))
    pending = failed = completed = 0
    for row in rows:
        v = row["vector"]
        c = row["count"]
        if v == Status.COMPLETED:
            completed += c
        elif v == Status.FAILED:
            failed += c
        else:
            pending += c
    total = pending + failed + completed
    return {
        "pending": pending,
        "failed": failed,
        "completed": completed,
        "total": total,
    }


def _document_qs(workspace: str | None):
    qs = Document.objects.all()
    if workspace:
        qs = qs.filter(workspace__name=workspace)
    return qs


def _chunk_qs(workspace: str | None):
    qs = DocumentChunk.objects.all()
    if workspace:
        qs = qs.filter(document__workspace__name=workspace)
    return qs


def _entity_qs(workspace: str | None):
    qs = KnowledgeEntity.objects.all()
    if workspace:
        qs = qs.filter(document__workspace__name=workspace)
    return qs


def _relation_qs(workspace: str | None):
    qs = KnowledgeRelation.objects.all()
    if workspace:
        qs = qs.filter(document__workspace__name=workspace)
    return qs


def _chunk_queue_busy(connection) -> bool:
    """
    True when chunk work is actively running.

    Stale StartedJobRegistry entries after a worker crash can outlive the worker;
    only queued jobs and live busy chunk workers count as real backlog.
    """
    chunk_queue = getattr(settings, "RQ_QUEUE_CHUNK", "chunk")
    counts = _queue_counts(chunk_queue, connection)
    if counts["queued"] > 0:
        return True
    try:
        for worker in Worker.all(connection=connection):
            if chunk_queue not in worker.queue_names():
                continue
            if worker.get_state() == "busy" and worker.get_current_job_id():
                return True
    except Exception:
        logger.warning(
            "queue_status: could not inspect chunk workers; treating queue as idle",
            exc_info=True,
        )
    return False


def _orphaned_chunk_count(*, workspace: str | None = None) -> int:
    """
    Chunks marked QUEUED/INPROGRESS in Postgres while the chunk RQ queue is idle.

    Indicates work lost after a worker/Redis restart; run recover_preprocess or
    restart worker-orchestrator with PREPROCESS_RECOVERY_ON_STARTUP enabled.
    """
    connection = get_connection()
    if _chunk_queue_busy(connection):
        return 0
    return _chunk_qs(workspace).filter(
        status__in=[Status.QUEUED, Status.INPROGRESS],
    ).count()


def build_database_backlog(*, workspace: str | None = None) -> dict[str, Any]:
    pending_failed = [Status.PENDING, Status.FAILED]
    result: dict[str, Any] = {
        "documents": _status_counts(_document_qs(workspace)),
        "chunks": _status_counts(_chunk_qs(workspace)),
        "chunks_orphaned": _orphaned_chunk_count(workspace=workspace),
        "vectors": {
            "entities": _vector_backlog_counts(
                _entity_qs(workspace).filter(vector__in=pending_failed)
            ),
            "relations": _vector_backlog_counts(
                _relation_qs(workspace).filter(vector__in=pending_failed)
            ),
            "chunks": _vector_backlog_counts(
                _chunk_qs(workspace).filter(vector__in=pending_failed)
            ),
        },
    }

    if workspace:
        return result

    incomplete: list[dict[str, Any]] = []
    for ws in Workspace.objects.order_by("name")[:MAX_INCOMPLETE_WORKSPACES]:
        status = build_workspace_preprocess_status(ws)
        overall = status["overall"]
        if overall["documents_total"] == 0:
            continue
        if overall.get("ready"):
            continue
        incomplete.append(
            {
                "workspace": ws.name,
                "phase": overall["phase"],
                "documents_total": overall["documents_total"],
                "documents_failed": overall["documents_failed"],
            }
        )
    result["workspaces_incomplete"] = incomplete
    return result


def _orchestrator_queue_names() -> tuple[str, ...]:
    return (
        getattr(settings, "RQ_QUEUE_ORCHESTRATOR_HIGH", "high"),
        getattr(settings, "RQ_QUEUE_ORCHESTRATOR", "orchestrator"),
        getattr(settings, "RQ_QUEUE_ORCHESTRATOR_LOW", "low"),
    )


def _orchestrator_jobs_for_workspace(workspace: str, connection) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for queue_name in _orchestrator_queue_names():
        jobs.extend(_queue_jobs(queue_name, connection, workspace=workspace))
    return jobs


def _workspaces_from_orchestrator_jobs(connection) -> set[str]:
    names: set[str] = set()
    for queue_name in _orchestrator_queue_names():
        queue = django_rq.get_queue(queue_name)
        job_ids: list[str] = []
        for job in queue.get_jobs():
            job_ids.append(job.id)
        started = StartedJobRegistry(queue_name, connection=connection)
        job_ids.extend(started.get_job_ids())
        for job in _fetch_jobs(job_ids, connection):
            summary = _parse_args_summary(job.func_name, job.args or (), job.kwargs or {})
            if summary.get("workspace"):
                names.add(summary["workspace"])
    return names


def build_active_pipelines(
    locks: list[dict[str, Any]], *, workspace: str | None = None
) -> list[dict[str, Any]]:
    connection = get_connection()
    lock_by_ws = {item["workspace"]: item for item in locks}
    try:
        workspaces = set(lock_by_ws) | _workspaces_from_orchestrator_jobs(connection)
    except Exception:
        logger.exception("queue_status: failed to scan orchestrator for active pipelines")
        workspaces = set(lock_by_ws)

    if workspace:
        workspaces = {workspace} if workspace in workspaces else set()

    active: list[dict[str, Any]] = []
    for ws_name in sorted(workspaces):
        lock = lock_by_ws.get(ws_name)
        try:
            orch_jobs = _orchestrator_jobs_for_workspace(ws_name, connection)
        except Exception:
            logger.exception(
                "queue_status: failed orchestrator jobs for workspace=%s", ws_name
            )
            orch_jobs = []
        if not lock and not orch_jobs:
            continue
        active.append(
            {
                "workspace": ws_name,
                "lock_held": lock is not None,
                "lock_ttl_seconds": lock["ttl_seconds"] if lock else None,
                "orchestrator_jobs": orch_jobs,
            }
        )
    return active


def build_queue_status(*, workspace: str | None = None) -> dict[str, Any]:
    """
    Full operational snapshot. Optional workspace filters job lists and DB scope;
    global lock scan still returns all locks (use active_pipelines for focus).
    """
    ws = (workspace or "").strip() or None
    locks = _scan_pipeline_locks()
    if ws:
        locks = [lock for lock in locks if lock["workspace"] == ws]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_filter": ws,
        "rq": build_rq_snapshot(workspace=ws),
        "redis": {"pipeline_locks": locks},
        "database": build_database_backlog(workspace=ws),
        "active_pipelines": build_active_pipelines(locks, workspace=ws),
    }
