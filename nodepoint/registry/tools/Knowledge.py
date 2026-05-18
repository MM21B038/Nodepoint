"""
active: true
"""

from nodepoint.registry import Tool
from nodepoint.services import kg_search
from nodepoint.services.chat_context import resolve_search_workspace_names
from nodepoint.services.kg_hybrid_search import hybrid_search
from nodepoint.services.kg_records import (
    RecordAccessError,
    RecordNotFoundError,
    format_name_search_markdown,
    format_record_markdown,
    get_chunk,
    get_document,
    get_entity,
    get_relation,
    search_entities_by_name,
)


def _no_workspace_message(query: str) -> str:
    return (
        f'# Knowledge search: "{query}"\n\n'
        "No workspace is flagged (starred). Star at least one workspace "
        "(is_flag=true) to include it in knowledge search."
    )


def _workspace_scope():
    workspaces = resolve_search_workspace_names()
    if not workspaces:
        return None
    return workspaces


@Tool.tool(
    server="Knowledge",
    description=(
        "Hybrid semantic + lexical search over workspace knowledge graphs. "
        "Optional record_type filter (entity, relation, chunk). Returns structured "
        "markdown with ids, chunk_id, content, and score breakdown."
    ),
)
def search_graph(
    query: str,
    limit: int = 10,
    record_type: str | None = None,
    candidate_limit: int = 4000,
    semantic_weight: float = 0.6,
    lexical_weight: float = 0.4,
    bm25_weight: float = 0.5,
) -> str:
    workspaces = _workspace_scope()
    if not workspaces:
        return _no_workspace_message(query)

    if record_type is not None:
        record_type = record_type.strip().lower()
        if record_type not in ("entity", "relation", "chunk"):
            return (
                f'# Knowledge search: "{query}"\n\n'
                "record_type must be entity, relation, or chunk."
            )

    records = hybrid_search(
        query,
        workspaces,
        limit=limit,
        record_type=record_type,
        candidate_limit=candidate_limit,
        semantic_weight=semantic_weight,
        lexical_weight=lexical_weight,
        bm25_weight=bm25_weight,
    )
    return kg_search.build_search_document_from_records(query, records)


@Tool.tool(
    server="Knowledge",
    description="Load a knowledge graph entity by UUID with full content and chunk_id.",
)
def get_entity_record(entity_id: str) -> str:
    workspaces = _workspace_scope()
    if not workspaces:
        return "No workspace is flagged for knowledge access."
    try:
        rec = get_entity(entity_id, allowed_workspaces=workspaces)
    except RecordNotFoundError as exc:
        return f"# Entity not found\n\n{exc}\n"
    except RecordAccessError as exc:
        return f"# Entity access denied\n\n{exc}\n"
    return format_record_markdown(rec)


@Tool.tool(
    server="Knowledge",
    description="Load a knowledge graph relation by UUID with full content and chunk_id.",
)
def get_relation_record(relation_id: str) -> str:
    workspaces = _workspace_scope()
    if not workspaces:
        return "No workspace is flagged for knowledge access."
    try:
        rec = get_relation(relation_id, allowed_workspaces=workspaces)
    except RecordNotFoundError as exc:
        return f"# Relation not found\n\n{exc}\n"
    except RecordAccessError as exc:
        return f"# Relation access denied\n\n{exc}\n"
    return format_record_markdown(rec)


@Tool.tool(
    server="Knowledge",
    description="Load a document chunk by UUID with full text content.",
)
def get_chunk_record(chunk_id: str) -> str:
    workspaces = _workspace_scope()
    if not workspaces:
        return "No workspace is flagged for knowledge access."
    try:
        rec = get_chunk(chunk_id, allowed_workspaces=workspaces)
    except RecordNotFoundError as exc:
        return f"# Chunk not found\n\n{exc}\n"
    except RecordAccessError as exc:
        return f"# Chunk access denied\n\n{exc}\n"
    return format_record_markdown(rec)


# @Tool.tool(
#     server="Knowledge",
#     description="Load full document text by document UUID (cite as [doc](id)).",
# )
# def get_document_record(document_id: str) -> str:
#     workspaces = _workspace_scope()
#     if not workspaces:
#         return "No workspace is flagged for knowledge access."
#     try:
#         rec = get_document(document_id, allowed_workspaces=workspaces)
#     except RecordNotFoundError as exc:
#         return f"# Document not found\n\n{exc}\n"
#     except RecordAccessError as exc:
#         return f"# Document access denied\n\n{exc}\n"
#     return format_record_markdown(rec)


@Tool.tool(
    server="Knowledge",
    description=(
        "Find entities by name with related relations. "
        "Default: fuzzy match (rapidfuzz) with threshold 0.6; set exact=true for case-insensitive exact name. "
        "Returns ids, chunk_id, content, and relationship data."
    ),
)
def search_entity_by_name(
    name: str,
    exact: bool = False,
    limit: int = 20,
    threshold: float = 0.6,
) -> str:
    workspaces = _workspace_scope()
    if not workspaces:
        return (
            f'# Entity name search: "{name}"\n\n'
            "No workspace is flagged (starred) for knowledge search."
        )
    if threshold < 0.0 or threshold > 1.0:
        return (
            f'# Entity name search: "{name}"\n\n'
            "threshold must be between 0 and 1."
        )
    matches = search_entities_by_name(
        name,
        workspaces,
        exact=exact,
        limit=limit,
        threshold=threshold,
    )
    return format_name_search_markdown(name, matches)
