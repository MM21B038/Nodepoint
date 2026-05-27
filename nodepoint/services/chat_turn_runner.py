from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from channels.layers import get_channel_layer

from nodepoint.agent.agent import Agent
from nodepoint.registry import Thread
from nodepoint.services import chat_runner, chat_stream_format, chat_turn_registry

logger = logging.getLogger(__name__)

RECONNECT_HINT = (
    "Refresh chat history via REST for content received while offline; "
    "live stream continues from reconnect."
)


def conversation_channel_group(conversation_id: uuid.UUID) -> str:
    return f"chat_{conversation_id}"


async def publish_frame(conversation_id: uuid.UUID, frame: dict[str, Any]) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send(
        conversation_channel_group(conversation_id),
        {"type": "chat.stream", "event": frame},
    )


async def publish_frames(
    conversation_id: uuid.UUID, frames: list[dict[str, Any]]
) -> None:
    for frame in frames:
        await publish_frame(conversation_id, frame)


async def start_turn(
    *,
    conversation_id: uuid.UUID,
    branch_id: uuid.UUID,
    thread: Thread,
    workspace_name: str | None,
    group_name: str | None,
    tools: list[dict[str, Any]],
    exclude_servers: set[str],
) -> uuid.UUID:
    turn_id = uuid.uuid4()
    task = asyncio.create_task(
        _run_turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            thread=thread,
            workspace_name=workspace_name,
            group_name=group_name,
            tools=tools,
            exclude_servers=exclude_servers,
        ),
        name=f"chat-turn-{conversation_id}",
    )
    await chat_turn_registry.register(conversation_id, task, turn_id)
    return turn_id


async def _run_turn(
    *,
    turn_id: uuid.UUID,
    conversation_id: uuid.UUID,
    branch_id: uuid.UUID,
    thread: Thread,
    workspace_name: str | None,
    group_name: str | None,
    tools: list[dict[str, Any]],
    exclude_servers: set[str],
) -> None:
    agent = Agent()
    formatter = chat_stream_format.ChatStreamFormatter()

    async def on_event(payload: dict[str, Any]) -> None:
        await publish_frames(conversation_id, formatter.format(payload))

    try:
        _, new_branch_id = await chat_runner.run_agent_stream(
            thread,
            agent,
            branch_id,
            conversation_id,
            workspace_name=workspace_name,
            group_name=group_name,
            tools=tools,
            exclude_servers=exclude_servers,
            on_event=on_event,
        )
        await publish_frames(conversation_id, formatter.close_sections())
        done_frame: dict[str, Any] = {"type": "chat.done", "turn_id": str(turn_id)}
        if new_branch_id:
            done_frame["active_branch_id"] = str(new_branch_id)
        await publish_frame(conversation_id, done_frame)
    except asyncio.CancelledError:
        await publish_frames(conversation_id, formatter.close_sections())
        await publish_frame(
            conversation_id,
            {"type": "chat.cancelled", "turn_id": str(turn_id)},
        )
        raise
    except Exception as exc:
        logger.exception(
            "Chat turn failed (conversation=%s turn=%s)", conversation_id, turn_id
        )
        await publish_frames(conversation_id, formatter.close_sections())
        await publish_frame(
            conversation_id,
            {"type": "error", "message": str(exc), "turn_id": str(turn_id)},
        )
    finally:
        await chat_turn_registry.unregister(conversation_id)
        await agent.aclose()
