from __future__ import annotations
from typing import List, Set

from contextvars import ContextVar, Token

from nodepoint.services.workspace_group import (
    GROUP_CHAT_PREFIX,
)

_chat_workspace: ContextVar[str | None] = ContextVar("chat_workspace", default=None)
_group_scope_chat: ContextVar[str | None] = ContextVar("group_scope_chat", default=None)
_search_ids: ContextVar[Set[str] | None] = ContextVar("search_ids", default=None)


def set_chat_workspace(workspace_name: str | None) -> Token:
    return _chat_workspace.set(workspace_name)


def reset_chat_workspace(token: Token) -> None:
    _chat_workspace.reset(token)


def get_chat_workspace() -> str | None:
    return _chat_workspace.get()


def set_group_scope_chat(group_name: str) -> Token:
    return _group_scope_chat.set(group_name)


def reset_group_scope_chat(token: Token) -> None:
    _group_scope_chat.reset(token)


def get_group_scope_chat() -> str | None:
    return _group_scope_chat.get()


def is_group_scope_chat() -> bool:
    return _group_scope_chat.get() is not None


def init_search_session() -> Token:
    return _search_ids.set(set())


def reset_search_session(token: Token) -> None:
    _search_ids.reset(token)


def get_accumulated_search_ids() -> Set[str]:
    current = _search_ids.get()
    return set(current) if current is not None else set()


def record_search_ids(ids: List[str]) -> None:
    current = _search_ids.get()
    if current is None:
        current = set()
        _search_ids.set(current)
    for rid in ids:
        if rid:
            current.add(rid)


def _is_internal_chat_workspace_name(name: str) -> bool:
    return name.startswith(GROUP_CHAT_PREFIX)


def resolve_search_workspace_names() -> List[str]:
    """
    Workspaces included in Knowledge.search_graph.

    Group-scope chat: workspaces derived from the active group's members.
    Per-workspace chat: the active workspace only.
    """
    from nodepoint.services.group_scope import resolve_active_group_search_scope

    scope = resolve_active_group_search_scope()
    if scope is not None:
        return scope.workspace_names

    chat_ws = get_chat_workspace()
    if chat_ws and not _is_internal_chat_workspace_name(chat_ws):
        return [chat_ws]
    return []


def resolve_active_group_scope():
    from nodepoint.services.group_scope import resolve_active_group_search_scope

    return resolve_active_group_search_scope()
