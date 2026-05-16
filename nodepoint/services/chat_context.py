from __future__ import annotations

from contextvars import ContextVar, Token

from nodepoint.models import Workspace
from nodepoint.services.workspace import (
    FLAGGED_CHAT_WORKSPACE_NAME,
    list_starred_workspace_names,
)

_chat_workspace: ContextVar[str | None] = ContextVar("chat_workspace", default=None)
_flagged_scope_chat: ContextVar[bool] = ContextVar("flagged_scope_chat", default=False)
_search_ids: ContextVar[set[str] | None] = ContextVar("search_ids", default=None)


def set_chat_workspace(workspace_name: str | None) -> Token:
    return _chat_workspace.set(workspace_name)


def reset_chat_workspace(token: Token) -> None:
    _chat_workspace.reset(token)


def get_chat_workspace() -> str | None:
    return _chat_workspace.get()


def set_flagged_scope_chat(enabled: bool = True) -> Token:
    return _flagged_scope_chat.set(enabled)


def reset_flagged_scope_chat(token: Token) -> None:
    _flagged_scope_chat.reset(token)


def is_flagged_scope_chat() -> bool:
    return _flagged_scope_chat.get()


def init_search_session() -> Token:
    return _search_ids.set(set())


def reset_search_session(token: Token) -> None:
    _search_ids.reset(token)


def get_accumulated_search_ids() -> set[str]:
    current = _search_ids.get()
    return set(current) if current is not None else set()


def record_search_ids(ids: list[str]) -> None:
    current = _search_ids.get()
    if current is None:
        current = set()
        _search_ids.set(current)
    for rid in ids:
        if rid:
            current.add(rid)


def resolve_search_workspace_names() -> list[str]:
    """
    Workspaces included in Knowledge.search_graph.

    Flagged-scope chat: only user-starred workspaces (is_flag=True).
    Per-workspace chat: current workspace plus all starred workspaces.
    """
    if is_flagged_scope_chat():
        return list_starred_workspace_names()

    names: set[str] = set(
        Workspace.objects.filter(is_flag=True)
        .exclude(name=FLAGGED_CHAT_WORKSPACE_NAME)
        .values_list("name", flat=True)
    )
    chat_ws = get_chat_workspace()
    if chat_ws and chat_ws != FLAGGED_CHAT_WORKSPACE_NAME:
        names.add(chat_ws)
    return sorted(names)
