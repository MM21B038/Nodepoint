from __future__ import annotations

import uuid
from typing import Any

from nodepoint.models import KnowledgeEntity, KnowledgeRelation
from nodepoint.services.chat_context import get_accumulated_search_ids, record_search_ids


def resolve_hits(hits: list[dict]) -> list[dict[str, Any]]:
    """Load full entity/relation rows for Qdrant hits (point id = Postgres PK)."""
    entity_ids: list[uuid.UUID] = []
    relation_ids: list[uuid.UUID] = []
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
        else:
            entity_ids.append(uid)

    records: list[dict[str, Any]] = []

    if entity_ids:
        for entity in KnowledgeEntity.objects.filter(id__in=entity_ids).select_related(
            "document", "document__workspace"
        ):
            records.append(
                {
                    "kind": "entity",
                    "id": str(entity.id),
                    "score": scores.get(str(entity.id), 0.0),
                    "name": entity.name,
                    "entity_type": entity.entity_type,
                    "attributes": entity.attributes,
                    "workspace": entity.document.workspace.name,
                    "file_name": entity.document.file_name,
                }
            )

    if relation_ids:
        for relation in KnowledgeRelation.objects.filter(id__in=relation_ids).select_related(
            "document",
            "document__workspace",
            "source",
            "target",
        ):
            records.append(
                {
                    "kind": "relation",
                    "id": str(relation.id),
                    "score": scores.get(str(relation.id), 0.0),
                    "source": relation.source.name,
                    "target": relation.target.name,
                    "type_description": relation.type_description,
                    "description": relation.description,
                    "workspace": relation.document.workspace.name,
                    "file_name": relation.document.file_name,
                }
            )

    records.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return records


def resolve_records_by_ids(record_ids: list[str]) -> list[dict[str, Any]]:
    if not record_ids:
        return []
    hits = [{"id": rid, "type": None, "score": 0.0, "payload": {}} for rid in record_ids]
    return resolve_hits(hits)


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
            "No matching entities or relations were found in the knowledge graph."
        )

    lines = [f'# Knowledge search: "{query}"', ""]
    for rec in records:
        file_name = rec.get("file_name") or "unknown"
        score = rec.get("score", 0.0)
        lines.append(f"## [source: {file_name}]")

        if rec.get("kind") == "entity":
            lines.append(f"**Entity** (score {score:.4f})")
            lines.append(f"- name: {rec.get('name', '')}")
            if rec.get("entity_type"):
                lines.append(f"- type: {rec['entity_type']}")
            if rec.get("attributes"):
                lines.append(f"- attributes: {rec['attributes']}")
            lines.append(f"- workspace: {rec.get('workspace', '')}")
        else:
            lines.append(f"**Relation** (score {score:.4f})")
            lines.append(
                f"- {rec.get('source', '')} — {rec.get('type_description') or 'related to'} — "
                f"{rec.get('target', '')}"
            )
            if rec.get("description"):
                lines.append(f"- description: {rec['description']}")
            lines.append(f"- workspace: {rec.get('workspace', '')}")

        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_search_document(query: str, hits: list[dict]) -> str:
    new_ids = [str(h["id"]) for h in hits if h.get("id")]
    all_ids = merge_with_session_hits(new_ids)
    record_search_ids(new_ids)

    records_by_id: dict[str, dict[str, Any]] = {}
    for rec in resolve_records_by_ids(all_ids):
        records_by_id[rec["id"]] = rec

    for rec in resolve_hits(hits):
        records_by_id[rec["id"]] = rec

    records = list(records_by_id.values())
    records.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return format_search_document(records, query)
