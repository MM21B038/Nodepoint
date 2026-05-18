from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from django.conf import settings

from nodepoint.agent.agent import Agent, tool_call_items_to_normalized
from nodepoint.agent.schema import (
    AgentSessionDoneEvent,
    AgentStreamEvent,
    AssistantResponseTokenEvent,
    AssistantToolCallsMessageEvent,
    ErrorEvent,
    ThinkingTokenEvent,
    ToolResultEvent,
)
from nodepoint.models import ChatMessageRole
from nodepoint.registry import Thread, Tool
from nodepoint.services import chat_compression, chat_context
from nodepoint.services import chat_storage_async as storage_async

logger = logging.getLogger(__name__)


async def run_agent_stream(
    thread: Thread,
    agent: Agent,
    branch_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    workspace_name: str | None = None,
    flagged_scope: bool = False,
    tools: list[dict[str, Any]] | None = None,
    exclude_servers: set[str] | None = None,
    on_event: Callable[[dict[str, Any]], Awaitable[None]],
) -> tuple[Thread, uuid.UUID | None]:
    """
    Stream agent events to ``on_event`` (JSON-serializable dicts).
    Mutates ``thread`` and persists messages to the DB.
    Returns (thread, new_branch_id_if_compressed).
    """
    pending_calls = None
    tools_remaining = 0
    thinking_buf: list[str] = []
    response_buf: list[str] = []
    new_branch_id: uuid.UUID | None = None

    if workspace_name is None and not flagged_scope:
        conversation = await storage_async.get_conversation(conversation_id)
        workspace_name = conversation.workspace.name

    if flagged_scope:
        flagged_token = chat_context.set_flagged_scope_chat(True)
        ctx_token = chat_context.set_chat_workspace(None)
    else:
        flagged_token = None
        ctx_token = chat_context.set_chat_workspace(workspace_name)
    search_token = chat_context.init_search_session()

    async def emit_typed(ev: AgentStreamEvent) -> None:
        payload = ev.model_dump(mode="json")
        payload["type"] = payload.get("type", ev.__class__.__name__)
        await on_event(payload)

    async def maybe_compress() -> None:
        nonlocal thread, new_branch_id
        token_count = await asyncio.to_thread(thread.root_count_tokens)
        if token_count < settings.CHAT_COMPRESS_TOKEN_THRESHOLD:
            return
        conversation = await storage_async.get_conversation(conversation_id)
        parent_branch = await storage_async.get_branch(branch_id)
        summary = await chat_compression.compress_async(agent, thread)
        new_branch = await storage_async.create_branch_from_compression(
            conversation, parent_branch, summary
        )
        new_branch_id = new_branch.id
        thread, _, _ = await storage_async.load_thread(new_branch.id)
        await on_event({"type": "chat.compressed"})

    try:
        async for ev in agent.stream_agent_events_async(
            messages=thread,
            tools=tools,
            register_mcp_tools=False,
        ):
            await emit_typed(ev)

            if isinstance(ev, ThinkingTokenEvent):
                thinking_buf.append(ev.token)
            elif isinstance(ev, AssistantResponseTokenEvent):
                response_buf.append(ev.token)
            elif isinstance(ev, AssistantToolCallsMessageEvent):
                thinking_buf.clear()
                response_buf.clear()
                thread.addAssistant(
                    {
                        "content": ev.content or "",
                        "tool_calls": [tc.model_dump(mode="json") for tc in ev.tool_calls],
                        "reasoning_content": ev.reasoning_content,
                    }
                )
                await storage_async.append_message(
                    branch_id,
                    role=ChatMessageRole.ASSISTANT,
                    content=ev.content or "",
                    reasoning_content=ev.reasoning_content,
                    tool_calls=[tc.model_dump(mode="json") for tc in ev.tool_calls],
                )
                pending_calls = tool_call_items_to_normalized(ev.tool_calls)
                tools_remaining = len(ev.tool_calls)
                await on_event(
                    {
                        "type": "tool_calls",
                        "names": [tc.function.name for tc in ev.tool_calls],
                    }
                )
            elif isinstance(ev, ToolResultEvent):
                if pending_calls is None:
                    continue
                call = next(
                    (c for c in pending_calls if (c.id or "") == ev.tool_call_id),
                    None,
                )
                if call is None:
                    call = next(
                        (c for c in pending_calls if c.name == ev.tool_name),
                        None,
                    )
                if call is not None:
                    thread.addTool(call, ev.result)
                    await storage_async.append_message(
                        branch_id,
                        role=ChatMessageRole.TOOL,
                        content=ev.result,
                        tool_call_id=ev.tool_call_id or call.id,
                        tool_name=ev.tool_name,
                    )
                await on_event(
                    {
                        "type": "tool_completed",
                        "tool_name": ev.tool_name,
                        "tool_call_id": ev.tool_call_id,
                        "ok": ev.ok,
                    }
                )
                tools_remaining -= 1
                if tools_remaining <= 0:
                    await maybe_compress()
            elif isinstance(ev, AgentSessionDoneEvent):
                text = "".join(response_buf)
                if text:
                    thread.addAssistant(text)
                    await storage_async.append_message(
                        branch_id,
                        role=ChatMessageRole.ASSISTANT,
                        content=text,
                        reasoning_content="".join(thinking_buf) or None,
                    )
                await maybe_compress()
                break
            elif isinstance(ev, ErrorEvent):
                await on_event({"type": "error", "message": ev.message})
                break
    except Exception as exc:
        logger.exception("Agent stream failed for branch %s", branch_id)
        await on_event({"type": "error", "message": str(exc)})
    finally:
        chat_context.reset_search_session(search_token)
        chat_context.reset_chat_workspace(ctx_token)
        if flagged_scope:
            chat_context.reset_flagged_scope_chat(flagged_token)

    return thread, new_branch_id


def default_tools(exclude_servers: set[str] | None = None) -> list[dict[str, Any]]:
    del exclude_servers
    return Tool.schemas(
        include_servers={"Knowledge"},
        include_tools={
            "Knowledge.search_graph",
            "Knowledge.get_entity_record",
            "Knowledge.get_relation_record",
            "Knowledge.get_chunk_record",
            "Knowledge.get_document_record",
            "Knowledge.search_entity_by_name",
        },
        include_mcp=False,
    )
