from __future__ import annotations

from dataclasses import dataclass

from rest_framework.request import Request

from nodepoint.services import kg_graph


@dataclass(frozen=True)
class KgScope:
    flagged: bool
    workspace_name: str | None


def resolve_kg_scope(request: Request) -> tuple[KgScope | None, str | None]:
    """
    Returns (scope, error_message). error_message is set for 400 responses.
    """
    workspace_name = request.query_params.get("workspace_name")
    flagged = request.query_params.get("flagged", "").lower() in (
        "1",
        "true",
        "yes",
    )

    if flagged and workspace_name:
        return None, "Use either workspace_name or flagged=true, not both"
    if not flagged and not workspace_name:
        return None, "Provide workspace_name or flagged=true"

    return KgScope(flagged=flagged, workspace_name=workspace_name), None


def parse_graph_filters_from_request(
    request: Request,
) -> tuple[kg_graph.GraphFilters | None, str | None]:
    try:
        filters = kg_graph.parse_graph_filters(
            entity_type_raw=request.query_params.get("entity_type"),
            depth_raw=request.query_params.get("depth"),
            limit_raw=request.query_params.get("limit"),
        )
    except ValueError as exc:
        return None, str(exc)
    return filters, None
