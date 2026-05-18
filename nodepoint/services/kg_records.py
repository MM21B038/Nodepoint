from __future__ import annotations

import uuid
from typing import Any

from nodepoint.models import Document, DocumentChunk, KnowledgeEntity, KnowledgeRelation
from nodepoint.mongo.manager import get_chunk_text, get_document_text


class RecordNotFoundError(LookupError):
    pass


class RecordAccessError(PermissionError):
    pass


CITATION_RULES = (
    "Cite sources ONLY as markdown links: [entity](uuid), [relation](uuid), "
    "[chunk](uuid), or [doc](uuid). "
    "Never use [source: file_name] or bare file names as citations."
)


def citation_for_record(rec: dict[str, Any]) -> str:
    """Primary citation link for a search/record row."""
    kind = rec.get("kind", "record")
    rid = rec.get("id", "")
    return f"[{kind}]({rid})"


def citation_metadata_lines(rec: dict[str, Any]) -> list[str]:
    """Standard metadata lines emphasizing id-based citations."""
    kind = rec.get("kind", "record")
    rid = rec.get("id", "")
    doc_id = rec.get("document_id")
    lines = [
        f"- **cite**: {citation_for_record(rec)}",
        f"- **id**: `{rid}`",
    ]
    if doc_id:
        lines.append(f"- **document**: [doc]({doc_id})")
    if kind in ("entity", "relation"):
        chunk_id = rec.get("chunk_id")
        if chunk_id:
            lines.append(f"- **chunk**: [chunk]({chunk_id})")
    workspace = rec.get("workspace")
    if workspace:
        lines.append(f"- **workspace**: {workspace}")
    return lines


def _entity_content(entity: KnowledgeEntity) -> str:
    parts = [f"name: {entity.name}"]
    if entity.entity_type:
        parts.append(f"type: {entity.entity_type}")
    if entity.attributes:
        parts.append(f"attributes: {entity.attributes}")
    return "\n".join(parts)


def _relation_content(relation: KnowledgeRelation) -> str:
    parts = [
        f"{relation.source.name} — {relation.type_description or 'related to'} — {relation.target.name}"
    ]
    if relation.description:
        parts.append(f"description: {relation.description}")
    return "\n".join(parts)


def record_search_text(kind: str, *, name: str = "", entity_type: str | None = None,
                       attributes: dict | None = None, source: str = "", target: str = "",
                       type_description: str | None = None, description: str | None = None,
                       file_name: str = "", chunk_index: int = 0, text: str = "") -> str:
    """Plain text used for BM25/fuzzy reranking (aligned with vector ingest)."""
    if kind == "entity":
        attributes = attributes or {}
        attr_str = ", ".join(f"{k}: {v}" for k, v in attributes.items())
        return f"{name} entity of type {entity_type or ''} with attributes {attr_str}"
    if kind == "relation":
        return f"{source} {type_description or 'related to'} {target}. {description or ''}"
    if kind == "chunk":
        snippet = text[:2000] if len(text) > 2000 else text
        return f"Document chunk {chunk_index} from {file_name}: {snippet}"
    return ""


def serialize_entity(entity: KnowledgeEntity) -> dict[str, Any]:
    return {
        "kind": "entity",
        "id": str(entity.id),
        "chunk_id": str(entity.chunk_id) if entity.chunk_id else None,
        "document_id": str(entity.document_id),
        "file_name": entity.document.file_name,
        "workspace": entity.document.workspace.name,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "attributes": entity.attributes,
        "content": _entity_content(entity),
    }


def serialize_relation(relation: KnowledgeRelation) -> dict[str, Any]:
    return {
        "kind": "relation",
        "id": str(relation.id),
        "chunk_id": str(relation.chunk_id) if relation.chunk_id else None,
        "document_id": str(relation.document_id),
        "file_name": relation.document.file_name,
        "workspace": relation.document.workspace.name,
        "source_entity_id": str(relation.source_id),
        "target_entity_id": str(relation.target_id),
        "source": relation.source.name,
        "target": relation.target.name,
        "type_description": relation.type_description,
        "description": relation.description,
        "content": _relation_content(relation),
    }


def serialize_chunk(chunk: DocumentChunk, *, full_content: bool = True) -> dict[str, Any]:
    text = get_chunk_text(chunk.id) or ""
    content = text if full_content else (text[:500] + ("..." if len(text) > 500 else ""))
    return {
        "kind": "chunk",
        "id": str(chunk.id),
        "chunk_id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "file_name": chunk.document.file_name,
        "workspace": chunk.document.workspace.name,
        "chunk_index": chunk.index,
        "content": content,
    }


def serialize_document(document: Document, *, full_content: bool = True) -> dict[str, Any]:
    text = get_document_text(document.id) or ""
    if not full_content and len(text) > 2000:
        text = text[:2000] + "..."
    return {
        "kind": "doc",
        "id": str(document.id),
        "document_id": str(document.id),
        "file_name": document.file_name,
        "workspace": document.workspace.name,
        "content": text,
    }


def _check_workspace(record_workspace: str, allowed_workspaces: list[str] | None) -> None:
    if allowed_workspaces is not None and record_workspace not in allowed_workspaces:
        raise RecordAccessError(f"Record is not in allowed workspaces: {allowed_workspaces}")


def get_entity(
    entity_id: uuid.UUID | str,
    *,
    allowed_workspaces: list[str] | None = None,
) -> dict[str, Any]:
    try:
        uid = uuid.UUID(str(entity_id))
    except ValueError as exc:
        raise RecordNotFoundError(f"Invalid entity id: {entity_id}") from exc
    try:
        entity = KnowledgeEntity.objects.select_related(
            "document", "document__workspace", "chunk"
        ).get(id=uid)
    except KnowledgeEntity.DoesNotExist as exc:
        raise RecordNotFoundError(f"Entity not found: {entity_id}") from exc
    _check_workspace(entity.document.workspace.name, allowed_workspaces)
    return serialize_entity(entity)


def get_relation(
    relation_id: uuid.UUID | str,
    *,
    allowed_workspaces: list[str] | None = None,
) -> dict[str, Any]:
    try:
        uid = uuid.UUID(str(relation_id))
    except ValueError as exc:
        raise RecordNotFoundError(f"Invalid relation id: {relation_id}") from exc
    try:
        relation = KnowledgeRelation.objects.select_related(
            "document",
            "document__workspace",
            "source",
            "target",
            "chunk",
        ).get(id=uid)
    except KnowledgeRelation.DoesNotExist as exc:
        raise RecordNotFoundError(f"Relation not found: {relation_id}") from exc
    _check_workspace(relation.document.workspace.name, allowed_workspaces)
    return serialize_relation(relation)


def get_chunk(
    chunk_id: uuid.UUID | str,
    *,
    allowed_workspaces: list[str] | None = None,
) -> dict[str, Any]:
    try:
        uid = uuid.UUID(str(chunk_id))
    except ValueError as exc:
        raise RecordNotFoundError(f"Invalid chunk id: {chunk_id}") from exc
    try:
        chunk = DocumentChunk.objects.select_related("document", "document__workspace").get(
            id=uid
        )
    except DocumentChunk.DoesNotExist as exc:
        raise RecordNotFoundError(f"Chunk not found: {chunk_id}") from exc
    _check_workspace(chunk.document.workspace.name, allowed_workspaces)
    return serialize_chunk(chunk, full_content=True)


def get_document(
    document_id: uuid.UUID | str,
    *,
    allowed_workspaces: list[str] | None = None,
) -> dict[str, Any]:
    try:
        uid = uuid.UUID(str(document_id))
    except ValueError as exc:
        raise RecordNotFoundError(f"Invalid document id: {document_id}") from exc
    try:
        document = Document.objects.select_related("workspace").get(id=uid)
    except Document.DoesNotExist as exc:
        raise RecordNotFoundError(f"Document not found: {document_id}") from exc
    _check_workspace(document.workspace.name, allowed_workspaces)
    return serialize_document(document, full_content=True)


def search_entities_by_name(
    name: str,
    workspaces: list[str],
    *,
    exact: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not workspaces or not name.strip():
        return []

    qs = KnowledgeEntity.objects.filter(
        document__workspace__name__in=workspaces,
    ).select_related("document", "document__workspace", "chunk")
    if exact:
        qs = qs.filter(name__iexact=name.strip())
    else:
        qs = qs.filter(name__icontains=name.strip())
    entities = list(qs[:limit])

    entity_ids = [e.id for e in entities]
    outgoing = KnowledgeRelation.objects.filter(source_id__in=entity_ids).select_related(
        "target", "source"
    )
    incoming = KnowledgeRelation.objects.filter(target_id__in=entity_ids).select_related(
        "source", "target"
    )

    relations_by_entity: dict[uuid.UUID, list[dict[str, Any]]] = {eid: [] for eid in entity_ids}

    for rel in outgoing:
        relations_by_entity[rel.source_id].append(
            {
                "relation_id": str(rel.id),
                "direction": "outgoing",
                "type_description": rel.type_description,
                "description": rel.description,
                "peer_entity_id": str(rel.target_id),
                "peer_entity_name": rel.target.name,
            }
        )
    for rel in incoming:
        relations_by_entity[rel.target_id].append(
            {
                "relation_id": str(rel.id),
                "direction": "incoming",
                "type_description": rel.type_description,
                "description": rel.description,
                "peer_entity_id": str(rel.source_id),
                "peer_entity_name": rel.source.name,
            }
        )

    results: list[dict[str, Any]] = []
    for entity in entities:
        rec = serialize_entity(entity)
        rec["relations"] = relations_by_entity.get(entity.id, [])
        results.append(rec)
    return results


def format_record_markdown(rec: dict[str, Any]) -> str:
    kind = rec.get("kind", "record")
    rid = rec.get("id", "")
    lines = [
        f"# {kind.title()} {citation_for_record(rec)}",
        "",
        f"> {CITATION_RULES}",
        "",
    ]
    lines.extend(citation_metadata_lines(rec))
    if kind == "doc" and rec.get("file_name"):
        lines.append(f"- **file_name** (label only, do not cite): {rec['file_name']}")
    if kind == "entity":
        if rec.get("entity_type"):
            lines.append(f"- **entity_type**: {rec['entity_type']}")
        if rec.get("attributes"):
            lines.append(f"- **attributes**: {rec['attributes']}")
    elif kind == "relation":
        lines.append(
            f"- **edge**: {rec.get('source', '')} — {rec.get('type_description') or 'related to'} — "
            f"{rec.get('target', '')}"
        )
    elif kind == "chunk":
        lines.append(f"- **chunk_index**: {rec.get('chunk_index', 0)}")
    lines.append("")
    lines.append("## Content")
    lines.append(rec.get("content") or "")
    if rec.get("relations"):
        lines.append("")
        lines.append("## Relations")
        for rel in rec["relations"]:
            lines.append(
                f"- [{rel['direction']}] [relation]({rel['relation_id']}): "
                f"{rel.get('type_description') or 'related to'} → "
                f"[entity]({rel['peer_entity_id']}) {rel['peer_entity_name']}"
            )
            if rel.get("description"):
                lines.append(f"  - {rel['description']}")
    return "\n".join(lines).rstrip() + "\n"


def format_name_search_markdown(name: str, matches: list[dict[str, Any]]) -> str:
    if not matches:
        return f'# Entity name search: "{name}"\n\nNo matching entities found.\n'
    lines = [
        f'# Entity name search: "{name}"',
        "",
        f"> {CITATION_RULES}",
        "",
        f"Found {len(matches)} match(es).",
        "",
    ]
    for i, rec in enumerate(matches, start=1):
        lines.append(f"## Match {i} — {citation_for_record(rec)}")
        lines.extend(citation_metadata_lines(rec))
        lines.append(f"- **name**: {rec.get('name', '')}")
        if rec.get("entity_type"):
            lines.append(f"- **type**: {rec['entity_type']}")
        lines.append("")
        lines.append("**content**:")
        lines.append(rec.get("content") or "")
        rels = rec.get("relations") or []
        if rels:
            lines.append("")
            lines.append("**relations**:")
            for rel in rels:
                lines.append(
                    f"- [{rel['direction']}] [relation]({rel['relation_id']}): "
                    f"{rel.get('type_description') or 'related to'} → "
                    f"[entity]({rel['peer_entity_id']}) {rel['peer_entity_name']}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
