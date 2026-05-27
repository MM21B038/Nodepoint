from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_turns: dict[uuid.UUID, TurnState] = {}


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
    async with _lock:
        existing = _turns.get(conversation_id)
        if existing is not None and not existing.task.done():
            existing.task.cancel()
        _turns[conversation_id] = TurnState(
            task=task,
            turn_id=turn_id,
            conversation_id=conversation_id,
            started_at=datetime.now(timezone.utc),
        )


async def unregister(conversation_id: uuid.UUID) -> None:
    async with _lock:
        _turns.pop(conversation_id, None)


async def get_state(conversation_id: uuid.UUID) -> TurnState | None:
    async with _lock:
        state = _turns.get(conversation_id)
        if state is None or state.task.done():
            return None
        return state


async def get_status(conversation_id: uuid.UUID) -> TurnStatus:
    state = await get_state(conversation_id)
    if state is None:
        return TurnStatus(active=False)
    return TurnStatus(
        active=True,
        turn_id=state.turn_id,
        started_at=state.started_at,
    )


async def is_turn_active(conversation_id: uuid.UUID) -> bool:
    return (await get_status(conversation_id)).active


async def cancel_turn(conversation_id: uuid.UUID) -> bool:
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
