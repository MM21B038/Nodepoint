"""
active: true
"""

from nodepoint.backend.vector import get_agent
from nodepoint.quadrant.manager import search_by_workspaces
from nodepoint.registry import Tool
from nodepoint.services import kg_search
from nodepoint.services.chat_context import resolve_search_workspace_names


@Tool.tool(
    server="Knowledge",
    description=(
        "Semantic search over workspace knowledge graphs. Scope is automatic "
        "(chat workspace plus flagged workspaces). Returns a markdown document with "
        "## [source: file_name] sections for citations. Use only this output to answer."
    ),
)
def search_graph(
    query: str,
    limit: int = 10,
    record_type: str | None = None,
) -> str:
    workspaces = resolve_search_workspace_names()
    if not workspaces:
        return (
            f'# Knowledge search: "{query}"\n\n'
            "No workspace is flagged (starred). Star at least one workspace "
            "(is_flag=true) to include it in knowledge search."
        )

    vector = get_agent().vector(query).squeeze().tolist()
    hits = search_by_workspaces(
        vector, workspaces, limit=limit, type_filter=record_type
    )
    return kg_search.build_search_document(query, hits)
