from __future__ import annotations

import uuid
from typing import Any

from channels.db import database_sync_to_async

from nodepoint.models import ChatBranch, ChatMessage, Conversation, Workspace
from nodepoint.registry import Thread
from nodepoint.services import chat_storage


@database_sync_to_async
def get_or_create_workspace_chat(workspace: Workspace) -> tuple[Conversation, ChatBranch]:
    return chat_storage.get_or_create_workspace_chat(workspace)


@database_sync_to_async
def get_active_branch(conversation_id: uuid.UUID) -> ChatBranch:
    return chat_storage.get_active_branch(conversation_id)


@database_sync_to_async
def load_thread(branch_id: uuid.UUID) -> tuple[Thread, ChatBranch, Conversation]:
    return chat_storage.load_thread(branch_id)


@database_sync_to_async
def append_message(branch_id: uuid.UUID, **kwargs: Any) -> ChatMessage:
    return chat_storage.append_message(branch_id, **kwargs)


@database_sync_to_async
def get_conversation(conversation_id: uuid.UUID) -> Conversation:
    return Conversation.objects.select_related("workspace").get(id=conversation_id)


@database_sync_to_async
def get_branch(branch_id: uuid.UUID) -> ChatBranch:
    return ChatBranch.objects.get(id=branch_id)


@database_sync_to_async
def create_branch_from_compression(
    conversation: Conversation,
    parent_branch: ChatBranch,
    summary: str,
) -> ChatBranch:
    return chat_storage.create_branch_from_compression(conversation, parent_branch, summary)
