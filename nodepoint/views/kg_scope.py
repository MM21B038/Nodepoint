from __future__ import annotations

from dataclasses import dataclass

from rest_framework.request import Request

from nodepoint.services import kg_graph


@dataclass(frozen=True)
class KgScope:
    workspace_name: str | None
    group_name: str | None

    @property
    def is_group_scope(self) -> bool:
        return self.group_name is not None


def resolve_kg_scope(request: Request) -> tuple[KgScope | None, str | None]:
    """
    Returns (scope, error_message). error_message is set for 400 responses.
    """
    workspace_name = (request.query_params.get("workspace_name") or "").strip() or None
    group_name = (request.query_params.get("group") or "").strip() or None

    scopes = sum(
        [
            bool(workspace_name),
            bool(group_name),
        ]
    )
    if scopes == 0:
        return None, "Provide workspace_name or group"
    if scopes > 1:
        return None, "Use either workspace_name or group, not both"

    return KgScope(workspace_name=workspace_name, group_name=group_name), None


def parse_graph_filters_from_request(
    request: Request,
) -> tuple[kg_graph.GraphFilters | None, str | None]:
    try:
        filters = kg_graph.parse_graph_filters(
            entity_type_raw=request.query_params.get("entity_type"),
            file_name_raw=request.query_params.get("file_name"),
            depth_raw=request.query_params.get("depth"),
            limit_raw=request.query_params.get("limit"),
        )
    except ValueError as exc:
        return None, str(exc)
    return filters, None
