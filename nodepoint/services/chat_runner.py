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
    group_name: str | None = None,
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
    segment_saved = False
    effective_branch_id = branch_id

    if workspace_name is None and group_name is None:
        conversation = await storage_async.get_conversation(conversation_id)
        workspace_name = conversation.workspace.name

    if group_name:
        group_token = chat_context.set_group_scope_chat(group_name)
        ctx_token = chat_context.set_chat_workspace(None)
    else:
        group_token = None
        ctx_token = chat_context.set_chat_workspace(workspace_name)
    search_token = chat_context.init_search_session()

    async def emit_typed(ev: AgentStreamEvent) -> None:
        payload = ev.model_dump(mode="json")
        payload["type"] = payload.get("type", ev.__class__.__name__)
        await on_event(payload)

    async def flush_streaming_segment(*, interrupted: bool = False) -> bool:
        """Persist in-progress assistant text (e.g. disconnect or cancel mid-stream)."""
        nonlocal segment_saved
        if segment_saved:
            return False
        text = "".join(response_buf)
        reasoning = "".join(thinking_buf) or None
        if not text and not reasoning:
            return False
        thread.addAssistant(text)
        await storage_async.append_message(
            effective_branch_id,
            role=ChatMessageRole.ASSISTANT,
            content=text,
            reasoning_content=reasoning,
        )
        segment_saved = True
        thinking_buf.clear()
        response_buf.clear()
        if interrupted:
            await on_event(
                {
                    "type": "chat.interrupted",
                    "message": "Response saved; reconnect or refresh history to continue.",
                }
            )
        return True

    async def maybe_compress() -> None:
        nonlocal thread, new_branch_id, effective_branch_id, segment_saved
        # Count the current active branch (not the root conversation), otherwise
        # compression can re-trigger immediately after switching to a compressed branch.
        token_count = await asyncio.to_thread(thread.count_tokens)
        threshold = settings.CHAT_COMPRESS_TOKEN_THRESHOLD
        if token_count < threshold:
            return
        logger.info(
            "context_compression_triggered conversation_id=%s branch_id=%s "
            "token_count=%s threshold=%s",
            conversation_id,
            effective_branch_id,
            token_count,
            threshold,
        )
        try:
            await on_event(
                {
                    "type": "chat.compress_started",
                    "message": "Summarizing conversation context to free window space…",
                }
            )
            conversation = await storage_async.get_conversation(conversation_id)
            parent_branch = await storage_async.get_branch(effective_branch_id)
            summary = await chat_compression.compress_async(agent, thread)
            await on_event(
                {
                    "type": "chat.compress_completed",
                    "message": "Context summary ready; continuing on a fresh branch.",
                    "summary_chars": len(summary),
                }
            )
            new_branch = await storage_async.create_branch_from_compression(
                conversation, parent_branch, summary
            )
            new_branch_id = new_branch.id
            effective_branch_id = new_branch.id
            segment_saved = False
            thinking_buf.clear()
            response_buf.clear()
            thread, _, _ = await storage_async.load_thread(new_branch.id)
            await on_event({"type": "chat.compressed"})
        except Exception as exc:
            logger.warning(
                "context_compression_skipped conversation_id=%s branch_id=%s "
                "token_count=%s error=%s",
                conversation_id,
                effective_branch_id,
                token_count,
                exc,
                exc_info=True,
            )
            await on_event(
                {
                    "type": "chat.compress_failed",
                    "message": (
                        "Context compression failed; continuing without compress. "
                        f"{exc}"
                    ),
                }
            )

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
                    effective_branch_id,
                    role=ChatMessageRole.ASSISTANT,
                    content=ev.content or "",
                    reasoning_content=ev.reasoning_content,
                    tool_calls=[tc.model_dump(mode="json") for tc in ev.tool_calls],
                )
                segment_saved = True
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
                        effective_branch_id,
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
            elif isinstance(ev, AgentSessionDoneEvent):
                text = "".join(response_buf)
                if text:
                    thread.addAssistant(text)
                    await storage_async.append_message(
                        effective_branch_id,
                        role=ChatMessageRole.ASSISTANT,
                        content=text,
                        reasoning_content="".join(thinking_buf) or None,
                    )
                    segment_saved = True
                # Only compress after the final assistant response is complete and persisted,
                # never mid-stream (avoids visible pauses / branch switches while streaming).
                await maybe_compress()
                break
            elif isinstance(ev, ErrorEvent):
                await flush_streaming_segment(interrupted=True)
                await on_event({"type": "error", "message": ev.message})
                break
    except asyncio.CancelledError:
        await flush_streaming_segment(interrupted=True)
        raise
    except Exception as exc:
        logger.exception("Agent stream failed for branch %s", effective_branch_id)
        await flush_streaming_segment(interrupted=True)
        await on_event({"type": "error", "message": str(exc)})
    finally:
        try:
            await flush_streaming_segment(interrupted=False)
        except Exception:
            logger.exception(
                "Failed to flush partial assistant for branch %s",
                effective_branch_id,
            )
        chat_context.reset_search_session(search_token)
        chat_context.reset_chat_workspace(ctx_token)
        if group_name:
            chat_context.reset_group_scope_chat(group_token)

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
