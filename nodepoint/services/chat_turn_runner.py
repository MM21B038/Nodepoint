from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from channels.layers import get_channel_layer
from django.conf import settings

from nodepoint.agent.agent import Agent
from nodepoint.registry import Thread
from nodepoint.services import chat_runner, chat_stream_format, chat_turn_registry
from nodepoint.services.chat_turn_registry import (
    ChatTurnCapacityExceeded,
    ChatTurnQueueAborted,
    ChatTurnQueueTimeout,
    TurnAlreadyActive,
)

logger = logging.getLogger(__name__)

RECONNECT_HINT = (
    "After chat.cancelled or chat.interrupted, merge saved from the frame or refresh "
    "REST once. While offline, live stream continues from reconnect."
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
    persist: bool = True,
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
            persist=persist,
        ),
        name=f"chat-turn-{conversation_id}",
    )
    try:
        await chat_turn_registry.register(conversation_id, task, turn_id)
    except (TurnAlreadyActive, ChatTurnCapacityExceeded):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        raise
    return turn_id


def _queue_timeout() -> float:
    return float(getattr(settings, "CHAT_TURN_QUEUE_TIMEOUT", 300))


def _queue_poll_interval() -> float:
    return max(0.1, float(getattr(settings, "CHAT_TURN_QUEUE_POLL_INTERVAL", 0.5)))


async def start_turn_queued(
    *,
    conversation_id: uuid.UUID,
    branch_id: uuid.UUID,
    thread: Thread,
    workspace_name: str | None,
    group_name: str | None,
    tools: list[dict[str, Any]],
    exclude_servers: set[str],
    persist: bool = True,
    on_queued: Callable[[], Awaitable[None]] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> uuid.UUID:
    """
    Start a turn, waiting in queue when the global parallel slot limit is reached.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _queue_timeout()
    queued_notified = False

    while True:
        if should_abort is not None and should_abort():
            raise ChatTurnQueueAborted()
        try:
            return await start_turn(
                conversation_id=conversation_id,
                branch_id=branch_id,
                thread=thread,
                workspace_name=workspace_name,
                group_name=group_name,
                tools=tools,
                exclude_servers=exclude_servers,
                persist=persist,
            )
        except TurnAlreadyActive:
            raise
        except ChatTurnCapacityExceeded:
            if loop.time() >= deadline:
                raise ChatTurnQueueTimeout()
            if on_queued is not None and not queued_notified:
                queued_notified = True
                await on_queued()
            await asyncio.sleep(_queue_poll_interval())


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
    persist: bool = True,
) -> None:
    agent = Agent()
    formatter = chat_stream_format.ChatStreamFormatter()
    interrupt_state: dict[str, Any] = {}

    async def on_event(payload: dict[str, Any]) -> None:
        await publish_frames(conversation_id, formatter.format(payload))

    try:
        thread, new_branch_id = await chat_runner.run_agent_stream(
            thread,
            agent,
            branch_id,
            conversation_id,
            workspace_name=workspace_name,
            group_name=group_name,
            tools=tools,
            exclude_servers=exclude_servers,
            on_event=on_event,
            interrupt_state=interrupt_state,
            persist=persist,
        )
        await publish_frames(conversation_id, formatter.close_sections())
        done_frame: dict[str, Any] = {"type": "chat.done", "turn_id": str(turn_id)}
        if new_branch_id:
            done_frame["active_branch_id"] = str(new_branch_id)
        await publish_frame(conversation_id, done_frame)
    except asyncio.CancelledError:
        await publish_frames(conversation_id, formatter.close_sections())
        cancelled_frame: dict[str, Any] = {
            "type": "chat.cancelled",
            "turn_id": str(turn_id),
        }
        saved = interrupt_state.get("saved")
        if saved:
            cancelled_frame["saved"] = saved
        await publish_frame(conversation_id, cancelled_frame)
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
        await chat_turn_registry.unregister(conversation_id, turn_id)
        await agent.aclose()
