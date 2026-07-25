"""
Operational snapshot of RQ queues, workers, Redis pipeline locks, and DB backlog.

Response shape is stable for GET /api/preprocess/queue-status/ — see docs/API.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Set, Tuple, cast
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
from nodepoint.services.preprocess_status import build_workspace_preprocess_overall

def _allowed_workspace_names_for_actor(actor) -> Set[str] | None:
    """None = no name filter (legacy); with actor, restrict to visible workspaces."""
    if actor is None:
        return None
    from nodepoint.auth.visibility import visible_workspaces_qs

    return set(visible_workspaces_qs(actor).values_list("name", flat=True))


def _apply_workspace_name_filter(names: Set[str], allowed_names: Set[str] | None) -> Set[str]:
    if allowed_names is None:
        return names
    return names & allowed_names

logger = logging.getLogger(__name__)

PIPELINE_LOCK_SCAN_PREFIX = "nodepoint:preprocess:pipeline:*"
MONITORED_QUEUES = ("high", "orchestrator", "low", "chunk", "vector", "default")
MAX_JOBS_LISTED = 40
MAX_FAILED_SAMPLE = 15
# Cap only for optional truncation metadata; scan uses all workspaces with documents.
MAX_INCOMPLETE_WORKSPACES = 500
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


def _job_args_tuple(job: Job) -> Tuple[Any, ...]:
    return tuple(job.args) if job.args else ()


def _parse_args_summary(
    func_name: str | None, args: Tuple[Any, ...], kwargs: Dict[str, Any]
) -> Dict[str, Any]:
    short = _func_short_name(func_name) or ""
    summary: Dict[str, Any] = {}

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
    summary = _parse_args_summary(job.func_name, _job_args_tuple(job), job.kwargs or {})
    if summary.get("workspace") == workspace:
        return True
    if workspace in str(job.args) or workspace in str(job.kwargs):
        return True
    return False


def _job_in_allowed_workspaces(job: Job, allowed_names: Set[str]) -> bool:
    summary = _parse_args_summary(job.func_name, _job_args_tuple(job), job.kwargs or {})
    ws = summary.get("workspace")
    if ws:
        return ws in allowed_names
    doc_id = summary.get("document_id")
    if doc_id:
        try:
            doc = Document.objects.filter(pk=doc_id).select_related("workspace").first()
            if doc and doc.workspace.name in allowed_names:
                return True
        except (ValueError, TypeError):
            pass
    return False


def _serialize_job(job: Job, *, origin_queue: str | None = None) -> Dict[str, Any]:
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
            job.func_name, _job_args_tuple(job), job.kwargs or {}
        ),
    }


def _fetch_jobs(job_ids: List[str], connection) -> List[Job]:
    jobs: List[Job] = []
    for job_id in job_ids:
        try:
            jobs.append(Job.fetch(job_id, connection=connection))
        except Exception:
            logger.debug("queue_status: could not fetch job %s", job_id, exc_info=True)
    return jobs


def _queue_counts(queue_name: str, connection) -> Dict[str, int]:
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


def _queue_jobs(
    queue_name: str,
    connection,
    *,
    workspace: str | None,
    allowed_names: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    queue = django_rq.get_queue(queue_name)
    seen: Set[str] = set()
    serialized: List[Dict[str, Any]] = []

    def add(job: Job, origin: str | None = None) -> None:
        if job.id in seen:
            return
        if workspace and not _job_matches_workspace(job, workspace):
            return
        if not workspace and allowed_names is not None and not _job_in_allowed_workspaces(
            job, allowed_names
        ):
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


def _failed_sample(
    queue_name: str,
    connection,
    *,
    workspace: str | None = None,
    allowed_names: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    registry = FailedJobRegistry(queue_name, connection=connection)
    sample: List[Dict[str, Any]] = []
    for job_id in registry.get_job_ids()[:MAX_FAILED_SAMPLE]:
        for job in _fetch_jobs([job_id], connection):
            if workspace and not _job_matches_workspace(job, workspace):
                continue
            if not workspace and allowed_names is not None and not _job_in_allowed_workspaces(
                job, allowed_names
            ):
                continue
            entry = _serialize_job(job, origin_queue=queue_name)
            entry["error"] = _truncate(job.exc_info or str(job.latest_result() or ""))
            sample.append(entry)
    return sample


def build_rq_snapshot(
    *, workspace: str | None = None, allowed_names: Set[str] | None = None
) -> Dict[str, Any]:
    connection = get_connection()
    queues: Dict[str, Any] = {}
    for name in MONITORED_QUEUES:
        if name not in settings.RQ_QUEUES:
            continue
        try:
            queues[name] = {
                "counts": _queue_counts(name, connection),
                "jobs": _queue_jobs(
                    name, connection, workspace=workspace, allowed_names=allowed_names
                ),
                "failed_sample": _failed_sample(
                    name,
                    connection,
                    workspace=workspace,
                    allowed_names=allowed_names,
                ),
            }
        except Exception:
            logger.exception("queue_status: failed to read queue %s", name)
            queues[name] = {"error": f"Could not read queue '{name}'"}

    workers: List[Dict[str, Any]] = []
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


def _scan_pipeline_locks() -> List[Dict[str, Any]]:
    try:
        conn = get_connection()
    except Exception:
        logger.debug("queue_status: could not connect to Redis for pipeline locks", exc_info=True)
        return []
    locks: List[Dict[str, Any]] = []
    try:
        key_iter = conn.scan_iter(match=PIPELINE_LOCK_SCAN_PREFIX)
    except Exception:
        logger.debug("queue_status: could not scan pipeline locks", exc_info=True)
        return []
    for key in key_iter:
        key_str = key.decode() if isinstance(key, bytes) else str(key)
        workspace = key_str.rsplit(":", 1)[-1]
        ttl = cast(int, conn.ttl(key))
        locks.append(
            {
                "workspace": workspace,
                "key": key_str,
                "ttl_seconds": ttl if ttl >= 0 else None,
            }
        )
    locks.sort(key=lambda x: x["workspace"])
    return locks


def _status_counts(qs, field: str = "status") -> Dict[str, int]:
    rows = qs.values(field).annotate(count=Count("id"))
    out: Dict[str, int] = {}
    total = 0
    for row in rows:
        status = row[field]
        count = row["count"]
        out[status] = count
        total += count
    out["total"] = total
    return out


def _vector_backlog_counts(qs) -> Dict[str, int]:
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


def _document_qs(
    workspace: str | None,
    allowed_names: Set[str] | None = None,
    *,
    workspace_id: int | None = None,
):
    qs = Document.objects.all()
    if workspace_id is not None:
        qs = qs.filter(workspace_id=workspace_id)
    elif workspace:
        qs = qs.filter(workspace__name=workspace)
    elif allowed_names is not None:
        qs = qs.filter(workspace__name__in=allowed_names)
    return qs


def _chunk_qs(
    workspace: str | None,
    allowed_names: Set[str] | None = None,
    *,
    workspace_id: int | None = None,
):
    qs = DocumentChunk.objects.all()
    if workspace_id is not None:
        qs = qs.filter(document__workspace_id=workspace_id)
    elif workspace:
        qs = qs.filter(document__workspace__name=workspace)
    elif allowed_names is not None:
        qs = qs.filter(document__workspace__name__in=allowed_names)
    return qs


def _entity_qs(
    workspace: str | None,
    allowed_names: Set[str] | None = None,
    *,
    workspace_id: int | None = None,
):
    qs = KnowledgeEntity.objects.all()
    if workspace_id is not None:
        qs = qs.filter(document__workspace_id=workspace_id)
    elif workspace:
        qs = qs.filter(document__workspace__name=workspace)
    elif allowed_names is not None:
        qs = qs.filter(document__workspace__name__in=allowed_names)
    return qs


def _relation_qs(
    workspace: str | None,
    allowed_names: Set[str] | None = None,
    *,
    workspace_id: int | None = None,
):
    qs = KnowledgeRelation.objects.all()
    if workspace_id is not None:
        qs = qs.filter(document__workspace_id=workspace_id)
    elif workspace:
        qs = qs.filter(document__workspace__name=workspace)
    elif allowed_names is not None:
        qs = qs.filter(document__workspace__name__in=allowed_names)
    return qs


def _chunk_ids_with_live_rq_jobs(connection=None) -> Set[UUID]:
    """Chunk IDs that have a waiting or started process_chunk job in Redis."""
    connection = connection or get_connection()
    chunk_queue = getattr(settings, "RQ_QUEUE_CHUNK", "chunk")
    live: Set[UUID] = set()
    try:
        queue = django_rq.get_queue(chunk_queue)
        job_ids = [job.id for job in queue.get_jobs()]
        started = StartedJobRegistry(chunk_queue, connection=connection)
        job_ids.extend(started.get_job_ids())
        for job in _fetch_jobs(job_ids, connection):
            if _func_short_name(job.func_name) != "process_chunk":
                continue
            summary = _parse_args_summary(
                job.func_name, _job_args_tuple(job), job.kwargs or {}
            )
            raw = summary.get("chunk_id")
            if raw:
                try:
                    live.add(UUID(str(raw)))
                except ValueError:
                    pass
    except Exception:
        logger.warning(
            "queue_status: could not list live chunk jobs; assuming none",
            exc_info=True,
        )
    return live


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


def _orphaned_chunk_count(
    *,
    workspace: str | None = None,
    allowed_names: Set[str] | None = None,
    workspace_id: int | None = None,
) -> int:
    """
    Chunks marked QUEUED/INPROGRESS in Postgres with no live process_chunk RQ job.

    Indicates work lost after a worker/Redis restart; run recover_preprocess or
    restart worker-orchestrator with PREPROCESS_RECOVERY_ON_STARTUP enabled.
    """
    live = _chunk_ids_with_live_rq_jobs()
    return (
        _chunk_qs(workspace, allowed_names, workspace_id=workspace_id)
        .filter(status__in=[Status.QUEUED, Status.INPROGRESS])
        .exclude(id__in=live)
        .count()
    )


_BACKLOG_DOCUMENT_STATUSES = (
    Status.PENDING,
    Status.QUEUED,
    Status.INPROGRESS,
    Status.FAILED,
    Status.INVALID,
    Status.TERMINATED,
)
_VECTOR_BACKLOG_STATUSES = (Status.PENDING, Status.FAILED)


def _workspace_names_with_db_backlog(
    allowed_names: Set[str] | None = None,
) -> Set[str]:
    """Workspace names that may still need preprocess (coarse DB signals)."""
    names: Set[str] = set()
    names.update(
        Document.objects.filter(status__in=_BACKLOG_DOCUMENT_STATUSES)
        .values_list("workspace__name", flat=True)
        .distinct()
    )
    doc_ids_with_chunks = DocumentChunk.objects.values("document_id").distinct()
    names.update(
        Document.objects.filter(status=Status.COMPLETED, content=True)
        .exclude(id__in=doc_ids_with_chunks)
        .values_list("workspace__name", flat=True)
        .distinct()
    )
    names.update(
        DocumentChunk.objects.exclude(status=Status.COMPLETED)
        .values_list("document__workspace__name", flat=True)
        .distinct()
    )
    for model in (KnowledgeEntity, KnowledgeRelation, DocumentChunk):
        names.update(
            model.objects.filter(vector__in=_VECTOR_BACKLOG_STATUSES)
            .values_list("document__workspace__name", flat=True)
            .distinct()
        )
    return _apply_workspace_name_filter(names, allowed_names)


def _workspaces_from_rq_queue_jobs(
    queue_names: Tuple[str, ...], connection, *, allowed_names: Set[str] | None = None
) -> Set[str]:
    names: Set[str] = set()
    for queue_name in queue_names:
        if queue_name not in settings.RQ_QUEUES:
            continue
        try:
            queue = django_rq.get_queue(queue_name)
            job_ids = [job.id for job in queue.get_jobs()]
            started = StartedJobRegistry(queue_name, connection=connection)
            job_ids.extend(started.get_job_ids())
            for job in _fetch_jobs(job_ids, connection):
                summary = _parse_args_summary(
                    job.func_name, _job_args_tuple(job), job.kwargs or {}
                )
                if summary.get("workspace"):
                    names.add(summary["workspace"])
        except Exception:
            logger.debug(
                "queue_status: could not scan queue %s for workspaces",
                queue_name,
                exc_info=True,
            )
    return _apply_workspace_name_filter(names, allowed_names)


def _collect_incomplete_workspace_candidate_names(
    *,
    active_pipelines: List[Dict[str, Any]] | None = None,
    pipeline_locks: List[Dict[str, Any]] | None = None,
    allowed_names: Set[str] | None = None,
) -> Set[str]:
    """
    Names likely not preprocess-ready — avoids scanning every workspace.

    Uses DB backlog queries, Redis locks, and RQ job args before per-workspace
    rollup. The superset is verified with build_workspace_preprocess_overall.
    """
    names = _workspace_names_with_db_backlog(allowed_names)
    connection = get_connection()
    names |= _workspaces_from_orchestrator_jobs(connection, allowed_names=allowed_names)
    names |= _workspaces_from_rq_queue_jobs(
        (
            getattr(settings, "RQ_QUEUE_CHUNK", "chunk"),
            getattr(settings, "RQ_QUEUE_VECTOR", "vector"),
        ),
        connection,
        allowed_names=allowed_names,
    )
    for entry in active_pipelines or []:
        ws = entry.get("workspace")
        if ws and (allowed_names is None or ws in allowed_names):
            names.add(ws)
    for lock in pipeline_locks or []:
        ws = lock.get("workspace")
        if ws and (allowed_names is None or ws in allowed_names):
            names.add(ws)
    return _apply_workspace_name_filter(names, allowed_names)


def _workspace_not_ready_row(
    ws: Workspace,
    *,
    overall: Dict[str, Any] | None = None,
    pipeline_active: bool = False,
    lock_held: bool = False,
    orchestrator_jobs: int = 0,
    phase_override: str | None = None,
) -> Dict[str, Any]:
    overall_stats: Dict[str, Any] = overall or build_workspace_preprocess_overall(ws)[
        "overall"
    ]
    phase = phase_override or overall_stats["phase"]
    return {
        "workspace": ws.name,
        "phase": phase,
        "documents_total": overall_stats["documents_total"],
        "documents_failed": overall_stats["documents_failed"],
        "chunks_orphaned": _orphaned_chunk_count(workspace=ws.name),
        "pipeline_active": pipeline_active,
        "lock_held": lock_held,
        "orchestrator_jobs": orchestrator_jobs,
    }


def _build_workspaces_incomplete_list(
    *,
    active_pipelines: List[Dict[str, Any]] | None = None,
    pipeline_locks: List[Dict[str, Any]] | None = None,
    allowed_names: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Workspaces that are not preprocess-ready in Postgres and/or have an active
    orchestrator pipeline (lock or queued/started jobs).

    Merges DB backlog with active_pipelines so the global overview lists
    workspaces that are running but may already show per-file ready in the DB.
    """
    active_pipelines = active_pipelines or []
    by_name: Dict[str, Dict[str, Any]] = {}
    candidates = _collect_incomplete_workspace_candidate_names(
        active_pipelines=active_pipelines,
        pipeline_locks=pipeline_locks,
        allowed_names=allowed_names,
    )
    if not candidates:
        return []

    for ws in Workspace.objects.filter(name__in=candidates).order_by("name"):
        overall = build_workspace_preprocess_overall(ws)["overall"]
        if overall["documents_total"] == 0:
            continue
        if overall.get("ready"):
            continue
        by_name[ws.name] = _workspace_not_ready_row(ws, overall=overall)

    for entry in active_pipelines:
        ws_name = entry["workspace"]
        if allowed_names is not None and ws_name not in allowed_names:
            continue
        job_count = len(entry.get("orchestrator_jobs") or [])
        lock_held = bool(entry.get("lock_held"))
        if ws_name in by_name:
            row = by_name[ws_name]
            row["pipeline_active"] = True
            row["lock_held"] = lock_held
            row["orchestrator_jobs"] = job_count
            continue
        try:
            ws = Workspace.objects.get(name=ws_name)
        except Workspace.DoesNotExist:
            continue
        overall = build_workspace_preprocess_overall(ws)["overall"]
        phase = overall["phase"] if not overall.get("ready") else "running"
        by_name[ws_name] = _workspace_not_ready_row(
            ws,
            overall=overall,
            pipeline_active=True,
            lock_held=lock_held,
            orchestrator_jobs=job_count,
            phase_override=phase,
        )

    rows = sorted(by_name.values(), key=lambda r: r["workspace"])
    if len(rows) > MAX_INCOMPLETE_WORKSPACES:
        logger.warning(
            "queue_status: %s incomplete workspace(s); list truncated to %s",
            len(rows),
            MAX_INCOMPLETE_WORKSPACES,
        )
        return rows[:MAX_INCOMPLETE_WORKSPACES]
    return rows


def build_workspaces_preprocess_summary(*, actor=None) -> Dict[str, Any]:
    """Lightweight list of not-ready workspaces visible to actor."""
    allowed = _allowed_workspace_names_for_actor(actor)
    locks = _scan_pipeline_locks()
    if allowed is not None:
        locks = [lock for lock in locks if lock["workspace"] in allowed]
    active = build_active_pipelines(locks, workspace=None, allowed_names=allowed)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspaces": _build_workspaces_incomplete_list(
            active_pipelines=active,
            pipeline_locks=locks,
            allowed_names=allowed,
        ),
    }


def build_database_backlog(
    *,
    workspace: str | None = None,
    workspace_id: int | None = None,
    active_pipelines: List[Dict[str, Any]] | None = None,
    pipeline_locks: List[Dict[str, Any]] | None = None,
    allowed_names: Set[str] | None = None,
) -> Dict[str, Any]:
    pending_failed = [Status.PENDING, Status.FAILED]
    scoped = workspace_id is not None or bool(workspace)
    result: Dict[str, Any] = {
        "documents": _status_counts(
            _document_qs(workspace, allowed_names, workspace_id=workspace_id)
        ),
        "chunks": _status_counts(
            _chunk_qs(workspace, allowed_names, workspace_id=workspace_id)
        ),
        "chunks_orphaned": _orphaned_chunk_count(
            workspace=workspace,
            allowed_names=allowed_names,
            workspace_id=workspace_id,
        ),
        "vectors": {
            "entities": _vector_backlog_counts(
                _entity_qs(workspace, allowed_names, workspace_id=workspace_id).filter(
                    vector__in=pending_failed
                )
            ),
            "relations": _vector_backlog_counts(
                _relation_qs(workspace, allowed_names, workspace_id=workspace_id).filter(
                    vector__in=pending_failed
                )
            ),
            "chunks": _vector_backlog_counts(
                _chunk_qs(workspace, allowed_names, workspace_id=workspace_id).filter(
                    vector__in=pending_failed
                )
            ),
        },
    }

    if scoped:
        return result

    result["workspaces_incomplete"] = _build_workspaces_incomplete_list(
        active_pipelines=active_pipelines,
        pipeline_locks=pipeline_locks,
        allowed_names=allowed_names,
    )
    return result


def _orchestrator_queue_names() -> Tuple[str, ...]:
    return (
        getattr(settings, "RQ_QUEUE_ORCHESTRATOR_HIGH", "high"),
        getattr(settings, "RQ_QUEUE_ORCHESTRATOR", "orchestrator"),
        getattr(settings, "RQ_QUEUE_ORCHESTRATOR_LOW", "low"),
    )


def _orchestrator_jobs_for_workspace(workspace: str, connection) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for queue_name in _orchestrator_queue_names():
        jobs.extend(_queue_jobs(queue_name, connection, workspace=workspace))
    return jobs


def _workspaces_from_orchestrator_jobs(
    connection, *, allowed_names: Set[str] | None = None
) -> Set[str]:
    names: Set[str] = set()
    try:
        for queue_name in _orchestrator_queue_names():
            queue = django_rq.get_queue(queue_name)
            job_ids: List[str] = []
            for job in queue.get_jobs():
                job_ids.append(job.id)
            started = StartedJobRegistry(queue_name, connection=connection)
            job_ids.extend(started.get_job_ids())
            for job in _fetch_jobs(job_ids, connection):
                summary = _parse_args_summary(
                    job.func_name, _job_args_tuple(job), job.kwargs or {}
                )
                if summary.get("workspace"):
                    names.add(summary["workspace"])
    except Exception:
        logger.debug(
            "queue_status: could not scan orchestrator queues for workspaces",
            exc_info=True,
        )
    return _apply_workspace_name_filter(names, allowed_names)


def build_active_pipelines(
    locks: List[Dict[str, Any]],
    *,
    workspace: str | None = None,
    allowed_names: Set[str] | None = None,
) -> List[Dict[str, Any]]:
    connection = get_connection()
    lock_by_ws = {item["workspace"]: item for item in locks}
    try:
        workspaces = set(lock_by_ws) | _workspaces_from_orchestrator_jobs(
            connection, allowed_names=allowed_names
        )
    except Exception:
        logger.exception("queue_status: failed to scan orchestrator for active pipelines")
        workspaces = set(lock_by_ws)

    if workspace:
        workspaces = {workspace} if workspace in workspaces else set()
    elif allowed_names is not None:
        workspaces &= allowed_names

    active: List[Dict[str, Any]] = []
    for ws_name in sorted(workspaces):
        lock = lock_by_ws.get(ws_name)
        try:
            orch_jobs = _orchestrator_jobs_for_workspace(ws_name, connection)
        except Exception:
            logger.exception(
                "queue_status: failed orchestrator jobs for workspace=%s", ws_name
            )
            orch_jobs: List[Dict[str, Any]] = []
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


def build_queue_status(
    *,
    workspace: str | None = None,
    workspace_id: int | None = None,
    actor=None,
) -> Dict[str, Any]:
    """
    Operational snapshot scoped to workspaces visible to actor when provided.
    When workspace_id is set, DB counts use that pk (disambiguates duplicate names).
    """
    ws = (workspace or "").strip() or None
    allowed = _allowed_workspace_names_for_actor(actor)
    if workspace_id is not None:
        if actor is not None:
            from nodepoint.auth.visibility import visible_workspaces_qs

            if not visible_workspaces_qs(actor).filter(pk=workspace_id).exists():
                raise PermissionError(f"Workspace not accessible: id={workspace_id}")
        if not ws:
            ws = (
                Workspace.objects.filter(pk=workspace_id)
                .values_list("name", flat=True)
                .first()
            )
    elif ws and allowed is not None and ws not in allowed:
        raise PermissionError(f"Workspace not accessible: {ws}")

    locks = _scan_pipeline_locks()
    if ws:
        locks = [lock for lock in locks if lock["workspace"] == ws]
    elif allowed is not None:
        locks = [lock for lock in locks if lock["workspace"] in allowed]

    active = build_active_pipelines(locks, workspace=ws, allowed_names=allowed)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_filter": ws,
        "rq": build_rq_snapshot(workspace=ws, allowed_names=allowed),
        "redis": {"pipeline_locks": locks},
        "database": build_database_backlog(
            workspace=ws,
            workspace_id=workspace_id,
            active_pipelines=active if not ws else None,
            pipeline_locks=locks if not ws else None,
            allowed_names=allowed,
        ),
        "active_pipelines": active,
    }
