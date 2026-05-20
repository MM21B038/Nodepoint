from __future__ import annotations

import uuid
from typing import Any

from nodepoint.models import DocumentChunk, KnowledgeEntity, KnowledgeRelation
from nodepoint.services.chat_context import (
    get_accumulated_search_ids,
    record_search_ids,
    resolve_search_workspace_names,
)
from nodepoint.services.kg_records import (
    CITATION_RULES,
    citation_for_record,
    citation_metadata_lines,
    serialize_chunk,
    serialize_entity,
    serialize_relation,
)


def resolve_hits(hits: list[dict]) -> list[dict[str, Any]]:
    """Load entity/relation/chunk rows for Qdrant hits (point id = Postgres PK)."""
    entity_ids: list[uuid.UUID] = []
    relation_ids: list[uuid.UUID] = []
    chunk_ids: list[uuid.UUID] = []
    scores: dict[str, float] = {}

    for hit in hits:
        hit_id = hit.get("id")
        if not hit_id:
            continue
        try:
            uid = uuid.UUID(str(hit_id))
        except ValueError:
            continue
        scores[str(uid)] = float(hit.get("score") or 0.0)
        record_type = hit.get("type") or (hit.get("payload") or {}).get("type")
        if record_type == "relation":
            relation_ids.append(uid)
        elif record_type == "chunk":
            chunk_ids.append(uid)
        else:
            entity_ids.append(uid)

    records: list[dict[str, Any]] = []

    if entity_ids:
        for entity in KnowledgeEntity.objects.filter(id__in=entity_ids).select_related(
            "document", "document__workspace", "chunk"
        ):
            rec = serialize_entity(entity)
            rec["score"] = scores.get(str(entity.id), 0.0)
            records.append(rec)

    if chunk_ids:
        for chunk in DocumentChunk.objects.filter(id__in=chunk_ids).select_related(
            "document", "document__workspace"
        ):
            rec = serialize_chunk(chunk, full_content=False)
            rec["score"] = scores.get(str(chunk.id), 0.0)
            records.append(rec)

    if relation_ids:
        for relation in KnowledgeRelation.objects.filter(id__in=relation_ids).select_related(
            "document",
            "document__workspace",
            "source",
            "target",
            "chunk",
        ):
            rec = serialize_relation(relation)
            rec["score"] = scores.get(str(relation.id), 0.0)
            records.append(rec)

    records.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return records


def _filter_records_by_workspace(
    records: list[dict[str, Any]],
    allowed_workspaces: list[str] | None,
) -> list[dict[str, Any]]:
    if not allowed_workspaces:
        return records
    allowed = set(allowed_workspaces)
    return [r for r in records if r.get("workspace") in allowed]


def resolve_records_by_ids(
    record_ids: list[str],
    *,
    allowed_workspaces: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not record_ids:
        return []
    hits = [{"id": rid, "type": None, "score": 0.0, "payload": {}} for rid in record_ids]
    records = resolve_hits(hits)
    if allowed_workspaces is None:
        allowed_workspaces = resolve_search_workspace_names()
    return _filter_records_by_workspace(records, allowed_workspaces)


def merge_with_session_hits(new_hit_ids: list[str]) -> list[str]:
    accumulated = list(get_accumulated_search_ids())
    seen = set(accumulated)
    for rid in new_hit_ids:
        if rid and rid not in seen:
            seen.add(rid)
            accumulated.append(rid)
    return accumulated


def format_search_document(records: list[dict[str, Any]], query: str) -> str:
    if not records:
        return (
            f'# Knowledge search: "{query}"\n\n'
            "No matching entities, relations, or chunks were found in the knowledge graph."
        )

    lines = [
        f'# Knowledge search: "{query}"',
        "",
        f"> {CITATION_RULES}",
        "",
    ]
    for i, rec in enumerate(records, start=1):
        kind = rec.get("kind", "record")
        scores = rec.get("scores") or {}
        total = scores.get("total", rec.get("score", 0.0))

        lines.append(f"## Result {i} — {citation_for_record(rec)}")
        lines.extend(citation_metadata_lines(rec))

        if scores:
            lines.append(
                "- **scores**: "
                f"total={scores.get('total', total):.4f} | "
                f"semantic={scores.get('semantic', 0):.4f} | "
                f"bm25={scores.get('bm25', 0):.4f} | "
                f"fuzzy={scores.get('fuzzy', 0):.4f} | "
                f"lexical={scores.get('lexical', 0):.4f}"
            )
        else:
            lines.append(f"- **score**: {float(rec.get('score', 0.0)):.4f}")

        lines.append("")
        lines.append("**content**:")
        if kind == "entity":
            lines.append(rec.get("content") or "")
            if rec.get("entity_type"):
                lines.append(f"- type: {rec['entity_type']}")
        elif kind == "chunk":
            lines.append(rec.get("content") or rec.get("snippet") or "")
        else:
            lines.append(rec.get("content") or "")
            lines.append(
                f"- edge: {rec.get('source', '')} — {rec.get('type_description') or 'related to'} — "
                f"{rec.get('target', '')}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_search_document(query: str, hits: list[dict]) -> str:
    allowed_workspaces = resolve_search_workspace_names()
    new_ids = [str(h["id"]) for h in hits if h.get("id")]
    all_ids = merge_with_session_hits(new_ids)
    record_search_ids(new_ids)

    records_by_id: dict[str, dict[str, Any]] = {}
    for rec in resolve_records_by_ids(all_ids, allowed_workspaces=allowed_workspaces):
        records_by_id[rec["id"]] = rec

    for rec in _filter_records_by_workspace(resolve_hits(hits), allowed_workspaces):
        records_by_id[rec["id"]] = rec

    records = list(records_by_id.values())
    records.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return format_search_document(records, query)


def build_search_document_from_records(
    query: str,
    records: list[dict[str, Any]],
    *,
    merge_session: bool = True,
) -> str:
    if merge_session:
        allowed_workspaces = resolve_search_workspace_names()
        new_ids = [r["id"] for r in records if r.get("id")]
        all_ids = merge_with_session_hits(new_ids)
        record_search_ids(new_ids)
        records_by_id: dict[str, dict[str, Any]] = {}
        for rec in resolve_records_by_ids(all_ids, allowed_workspaces=allowed_workspaces):
            records_by_id[rec["id"]] = rec
        for rec in _filter_records_by_workspace(records, allowed_workspaces):
            records_by_id[rec["id"]] = rec
        records = list(records_by_id.values())
        records.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return format_search_document(records, query)
