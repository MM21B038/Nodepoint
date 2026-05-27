from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max

from nodepoint.models import (
    ChatBranch,
    ChatMessage,
    ChatMessageRole,
    Conversation,
    Workspace,
)
from nodepoint.registry import Thread


def _create_conversation_with_root(workspace: Workspace) -> tuple[Conversation, ChatBranch]:
    system_prompt = settings.CHAT_DEFAULT_SYSTEM
    conversation = Conversation.objects.create(
        workspace=workspace,
        title="",
        system_prompt=system_prompt,
        compressions=[],
    )
    root = ChatBranch.objects.create(
        conversation=conversation,
        is_root=True,
        label="root",
    )
    if system_prompt:
        ChatMessage.objects.create(
            branch=root,
            sequence=0,
            role=ChatMessageRole.SYSTEM,
            content=system_prompt,
        )
    return conversation, root


def get_workspace_chat(workspace: Workspace) -> Conversation | None:
    return Conversation.objects.filter(workspace=workspace).first()


def get_or_create_workspace_chat(workspace: Workspace) -> tuple[Conversation, ChatBranch]:
    conversation = get_workspace_chat(workspace)
    if conversation is not None:
        return conversation, get_root_branch(conversation.id)
    with transaction.atomic():
        conversation = get_workspace_chat(workspace)
        if conversation is not None:
            return conversation, get_root_branch(conversation.id)
        return _create_conversation_with_root(workspace)


def clear_workspace_chat(workspace: Workspace) -> tuple[Conversation, ChatBranch]:
    Conversation.objects.filter(workspace=workspace).delete()
    return get_or_create_workspace_chat(workspace)


def create_conversation(workspace: Workspace) -> tuple[Conversation, ChatBranch]:
    """Backward-compatible alias; returns existing chat if present."""
    return get_or_create_workspace_chat(workspace)


def get_root_branch(conversation_id: uuid.UUID) -> ChatBranch:
    return ChatBranch.objects.get(conversation_id=conversation_id, is_root=True)


def get_active_branch(conversation_id: uuid.UUID) -> ChatBranch:
    """Tip of the branch chain (root → compression children). Used for agent context."""
    branch = get_root_branch(conversation_id)
    while True:
        child = (
            ChatBranch.objects.filter(parent=branch, conversation_id=conversation_id)
            .order_by("-created_at")
            .first()
        )
        if child is None:
            return branch
        branch = child


def list_visible_branches(conversation_id: uuid.UUID) -> list[ChatBranch]:
    return list(
        ChatBranch.objects.filter(
            conversation_id=conversation_id, is_internal=False
        ).order_by("created_at")
    )


def workspace_chat_summary(workspace: Workspace) -> dict[str, Any]:
    conversation = get_workspace_chat(workspace)
    if conversation is None:
        return {
            "workspace": workspace.name,
            "updated_at": None,
            "message_count": 0,
        }
    try:
        root = get_root_branch(conversation.id)
        message_count = ChatMessage.objects.filter(branch=root).count()
    except ChatBranch.DoesNotExist:
        message_count = 0
    return {
        "workspace": workspace.name,
        "updated_at": conversation.updated_at,
        "message_count": message_count,
    }


def list_chat_summary_for_workspace(workspace: Workspace) -> dict[str, Any]:
    return workspace_chat_summary(workspace)


def list_chat_summary_for_group(group_name: str) -> list[dict[str, Any]]:
    from nodepoint.services.workspace_group import get_group_workspaces_qs

    workspaces = get_group_workspaces_qs(group_name).order_by("name")
    return [workspace_chat_summary(ws) for ws in workspaces]


def load_root_messages(conversation_id: uuid.UUID) -> list[ChatMessage]:
    root = get_root_branch(conversation_id)
    return list(ChatMessage.objects.filter(branch=root).order_by("sequence"))


def list_branches(conversation_id: uuid.UUID) -> list[ChatBranch]:
    return list(
        ChatBranch.objects.filter(conversation_id=conversation_id).order_by("created_at")
    )


def load_branch_messages(branch_id: uuid.UUID) -> list[ChatMessage]:
    return list(ChatMessage.objects.filter(branch_id=branch_id).order_by("sequence"))


def _next_sequence(branch_id: uuid.UUID) -> int:
    current = (
        ChatMessage.objects.filter(branch_id=branch_id).aggregate(m=Max("sequence"))["m"]
    )
    return 0 if current is None else current + 1


COMPRESSION_HANDOFF_PREFIX = "Context handoff (compression):"


def is_compression_handoff_content(content: str) -> bool:
    text = (content or "").strip()
    return text.startswith(COMPRESSION_HANDOFF_PREFIX) or text.startswith(
        "max-token / window handoff"
    )


def append_message(
    branch_id: uuid.UUID,
    *,
    role: str,
    content: str = "",
    reasoning_content: str | None = None,
    tool_calls: list | dict | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> ChatMessage:
    return ChatMessage.objects.create(
        branch_id=branch_id,
        sequence=_next_sequence(branch_id),
        role=role,
        content=content or "",
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )


def append_message_visible(
    conversation_id: uuid.UUID,
    branch_id: uuid.UUID,
    *,
    role: str,
    content: str = "",
    reasoning_content: str | None = None,
    tool_calls: list | dict | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> ChatMessage:
    """
    Persist on the agent branch and mirror user-visible roles to the root branch.

    REST chat history (`load_root_messages`) only exposes the root branch; internal
    compression branches hold agent context. Mirroring keeps streamed turns visible.
    """
    msg = append_message(
        branch_id,
        role=role,
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    root = get_root_branch(conversation_id)
    if branch_id == root.id:
        return msg
    if role == ChatMessageRole.SYSTEM:
        return msg
    if role == ChatMessageRole.USER and is_compression_handoff_content(content):
        return msg
    append_message(
        root.id,
        role=role,
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    return msg


def sync_visible_messages_to_root(
    conversation_id: uuid.UUID, branch_id: uuid.UUID
) -> None:
    """Copy messages from an internal branch onto root (deduped), e.g. before compression."""
    root = get_root_branch(conversation_id)
    if branch_id == root.id:
        return
    root_keys = {
        (m.role, m.content or "", m.tool_call_id or "")
        for m in load_branch_messages(root.id)
        if m.role != ChatMessageRole.SYSTEM
    }
    for msg in load_branch_messages(branch_id):
        if msg.role == ChatMessageRole.SYSTEM:
            continue
        if msg.role == ChatMessageRole.USER and is_compression_handoff_content(msg.content):
            continue
        key = (msg.role, msg.content or "", msg.tool_call_id or "")
        if key in root_keys:
            continue
        append_message(
            root.id,
            role=msg.role,
            content=msg.content,
            reasoning_content=msg.reasoning_content,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
            tool_name=msg.tool_name,
        )
        root_keys.add(key)


def load_thread(branch_id: uuid.UUID) -> tuple[Thread, ChatBranch, Conversation]:
    branch = ChatBranch.objects.select_related("conversation").get(id=branch_id)
    conversation = branch.conversation
    thread = Thread()
    messages = load_branch_messages(branch_id)

    if conversation.system_prompt and not any(
        m.role == ChatMessageRole.SYSTEM for m in messages
    ):
        thread.addSystem(conversation.system_prompt)

    for msg in messages:
        if msg.role == ChatMessageRole.SYSTEM:
            thread.addSystem(msg.content)
        elif msg.role == ChatMessageRole.USER:
            thread.addUser(msg.content)
        elif msg.role == ChatMessageRole.ASSISTANT:
            if msg.tool_calls:
                thread.addAssistant(
                    {
                        "content": msg.content or "",
                        "tool_calls": msg.tool_calls,
                        "reasoning_content": msg.reasoning_content,
                    }
                )
            else:
                thread.addAssistant(msg.content)
        elif msg.role == ChatMessageRole.TOOL:
            from nodepoint.agent.agent import ToolCallNormalized

            call = ToolCallNormalized(
                name=msg.tool_name or "tool",
                args={},
                id=msg.tool_call_id or "",
            )
            thread.addTool(call, msg.content)

    for summary in conversation.compressions or []:
        thread.list_compression.append(summary)

    return thread, branch, conversation


def create_branch_from_compression(
    conversation: Conversation,
    parent_branch: ChatBranch,
    summary: str,
) -> ChatBranch:
    label = (summary[:200] + "…") if len(summary) > 200 else summary
    with transaction.atomic():
        sync_visible_messages_to_root(conversation.id, parent_branch.id)

        compressions = list(conversation.compressions or [])
        compressions.append(summary)
        conversation.compressions = compressions
        conversation.save(update_fields=["compressions", "updated_at"])

        new_branch = ChatBranch.objects.create(
            conversation=conversation,
            parent=parent_branch,
            is_root=False,
            is_internal=True,
            label=label,
        )
        handoff = f"{COMPRESSION_HANDOFF_PREFIX}\n\n{summary}"
        append_message(new_branch.id, role=ChatMessageRole.USER, content=handoff)
    return new_branch


def serialize_messages_for_api(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "reasoning_content": m.reasoning_content,
            "tool_calls": m.tool_calls,
            "tool_call_id": m.tool_call_id,
            "tool_name": m.tool_name,
            "sequence": m.sequence,
            "created_at": m.created_at,
        }
        for m in messages
    ]
