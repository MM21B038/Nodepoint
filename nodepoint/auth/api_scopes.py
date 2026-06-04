"""Stable API scope codes mapped to URL names and HTTP methods."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeDefinition:
    code: str
    description: str
    url_names: frozenset[str]
    methods: frozenset[str]


SCOPE_DEFINITIONS: tuple[ScopeDefinition, ...] = (
    ScopeDefinition(
        "workspace:read",
        "List and read workspaces",
        frozenset(
            {
                "workspace-list",
                "workspace-lookup",
                "workspace-stats",
                "workspace-page",
                "workspace-preprocess-status",
                "preprocess-queue-status",
                "preprocess-workspaces-summary",
            }
        ),
        frozenset({"GET"}),
    ),
    ScopeDefinition(
        "workspace:write",
        "Create, update, and delete workspaces",
        frozenset(
            {
                "workspace-create",
                "workspace-update",
                "workspace-delete",
            }
        ),
        frozenset({"POST", "PATCH", "DELETE"}),
    ),
    ScopeDefinition(
        "document:read",
        "List documents in a workspace",
        frozenset({"document-list"}),
        frozenset({"GET"}),
    ),
    ScopeDefinition(
        "document:write",
        "Upload and delete documents",
        frozenset({"document-upload", "document-delete"}),
        frozenset({"POST", "DELETE"}),
    ),
    ScopeDefinition(
        "chat:read",
        "Read chat sessions and summaries",
        frozenset(
            {
                "chat-summary",
                "workspace-chat-sessions",
                "workspace-chat-session-detail",
                "group-chat-sessions",
                "group-chat-session-detail",
            }
        ),
        frozenset({"GET"}),
    ),
    ScopeDefinition(
        "chat:write",
        "Create, update, and clear chat sessions",
        frozenset(
            {
                "workspace-chat-sessions",
                "workspace-chat-session-detail",
                "workspace-chat-session-clear",
                "group-chat-sessions",
                "group-chat-session-detail",
                "group-chat-session-clear",
            }
        ),
        frozenset({"POST", "PATCH", "DELETE"}),
    ),
    ScopeDefinition(
        "group:read",
        "List and read workspace groups",
        frozenset(
            {
                "group-list",
                "group-lookup",
                "group-detail",
                "group-members",
                "group-add-options",
                "workspace-group-options",
            }
        ),
        frozenset({"GET"}),
    ),
    ScopeDefinition(
        "group:write",
        "Create and manage workspace groups",
        frozenset(
            {
                "group-create",
                "group-detail",
                "group-add-workspace",
                "group-remove-workspace",
                "group-add-file",
                "group-remove-file",
                "group-add-entity",
                "group-remove-entity",
                "group-add-relation",
                "group-remove-relation",
            }
        ),
        frozenset({"POST", "PATCH", "DELETE"}),
    ),
    ScopeDefinition(
        "kg:read",
        "Knowledge graph and record reads",
        frozenset(
            {
                "knowledge-graph",
                "knowledge-graph-entity-types",
                "knowledge-entity-search",
                "knowledge-entity-detail",
                "knowledge-relation-detail",
                "knowledge-chunk-detail",
                "knowledge-document-detail",
            }
        ),
        frozenset({"GET"}),
    ),
    ScopeDefinition(
        "preprocess:read",
        "Preprocess queue and status reads",
        frozenset(
            {
                "preprocess-queue-status",
                "preprocess-workspaces-summary",
                "workspace-preprocess-status",
            }
        ),
        frozenset({"GET"}),
    ),
    ScopeDefinition(
        "preprocess:write",
        "Trigger workspace preprocessing",
        frozenset({"workspace-preprocess"}),
        frozenset({"POST"}),
    ),
)

SCOPE_CODES: frozenset[str] = frozenset(d.code for d in SCOPE_DEFINITIONS)

_URL_SCOPE_INDEX: dict[tuple[str, str], str] = {}
for _defn in SCOPE_DEFINITIONS:
    for _url_name in _defn.url_names:
        for _method in _defn.methods:
            _URL_SCOPE_INDEX[(_url_name, _method)] = _defn.code


def scope_for_request(url_name: str | None, method: str) -> str | None:
    if not url_name:
        return None
    return _URL_SCOPE_INDEX.get((url_name, method.upper()))


def list_scopes_for_api() -> list[dict[str, str]]:
    return [{"code": d.code, "description": d.description} for d in SCOPE_DEFINITIONS]
