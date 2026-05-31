from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from nodepoint.services import chat_turn_redis

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_turns: dict[uuid.UUID, TurnState] = {}


class TurnAlreadyActive(Exception):
    """Raised when Redis already holds an active turn for this conversation."""


@dataclass
class TurnState:
    task: asyncio.Task
    turn_id: uuid.UUID
    conversation_id: uuid.UUID
    started_at: datetime


@dataclass(frozen=True)
class TurnStatus:
    active: bool
    turn_id: uuid.UUID | None = None
    started_at: datetime | None = None


async def register(
    conversation_id: uuid.UUID,
    task: asyncio.Task,
    turn_id: uuid.UUID,
) -> None:
    started_at = datetime.now(timezone.utc)
    acquired = await asyncio.to_thread(
        chat_turn_redis.try_set_active_turn,
        conversation_id,
        turn_id,
        started_at,
    )
    if not acquired:
        raise TurnAlreadyActive(conversation_id)

    async with _lock:
        existing = _turns.get(conversation_id)
        if existing is not None and not existing.task.done():
            existing.task.cancel()
        _turns[conversation_id] = TurnState(
            task=task,
            turn_id=turn_id,
            conversation_id=conversation_id,
            started_at=started_at,
        )


async def unregister(conversation_id: uuid.UUID, turn_id: uuid.UUID | None = None) -> None:
    async with _lock:
        state = _turns.pop(conversation_id, None)
        if turn_id is None and state is not None:
            turn_id = state.turn_id
    if turn_id is not None:
        await asyncio.to_thread(
            chat_turn_redis.clear_active_turn,
            conversation_id,
            turn_id,
        )


async def get_state(conversation_id: uuid.UUID) -> TurnState | None:
    async with _lock:
        state = _turns.get(conversation_id)
        if state is None or state.task.done():
            return None
        return state


async def get_status(conversation_id: uuid.UUID) -> TurnStatus:
    record = await asyncio.to_thread(chat_turn_redis.get_active_turn, conversation_id)
    if record is None:
        return TurnStatus(active=False)
    return TurnStatus(
        active=True,
        turn_id=record.turn_id,
        started_at=record.started_at,
    )


async def is_turn_active(conversation_id: uuid.UUID) -> bool:
    return (await get_status(conversation_id)).active


async def cancel_local_turn(conversation_id: uuid.UUID) -> bool:
    """Cancel the asyncio task on this worker only (used by pub/sub listener)."""
    async with _lock:
        state = _turns.get(conversation_id)
        if state is None or state.task.done():
            if state is not None:
                _turns.pop(conversation_id, None)
            return False
        task = state.task
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return True


async def cancel_turn(conversation_id: uuid.UUID) -> bool:
    if await cancel_local_turn(conversation_id):
        return True
    if not await is_turn_active(conversation_id):
        return False
    await asyncio.to_thread(chat_turn_redis.publish_cancel, conversation_id)
    return True
