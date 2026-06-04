from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from django.conf import settings

from nodepoint.services import chat_turn_redis, chat_turn_slots

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_turns: dict[uuid.UUID, TurnState] = {}

CANCEL_WAIT_POLL_INTERVAL = 0.05


def _cancel_wait_timeout() -> float:
    return float(getattr(settings, "CHAT_CANCEL_WAIT_TIMEOUT", 10.0))


class TurnAlreadyActive(Exception):
    """Raised when Redis already holds an active turn for this conversation."""


class ChatTurnCapacityExceeded(Exception):
    """Raised when the global parallel chat turn limit is reached."""


class ChatTurnQueueTimeout(Exception):
    """Raised when a turn could not acquire a slot before the queue timeout."""


class ChatTurnQueueAborted(Exception):
    """Raised when a queued turn wait is cancelled (e.g. chat.cancel)."""


@dataclass
class TurnState:
    task: asyncio.Task
    turn_id: uuid.UUID
    conversation_id: uuid.UUID
    started_at: datetime
    global_slot_held: bool = False


@dataclass(frozen=True)
class TurnStatus:
    active: bool
    turn_id: uuid.UUID | None = None
    started_at: datetime | None = None


@dataclass(frozen=True)
class CancelResult:
    cancelled: bool
    turn_inactive: bool = False

    @property
    def flushed(self) -> bool:
        return self.cancelled and self.turn_inactive


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

    slot_acquired = await asyncio.to_thread(
        chat_turn_slots.try_acquire_turn_slot,
        conversation_id,
        turn_id,
    )
    if not slot_acquired:
        await asyncio.to_thread(
            chat_turn_redis.clear_active_turn,
            conversation_id,
            turn_id,
        )
        raise ChatTurnCapacityExceeded()

    async with _lock:
        existing = _turns.get(conversation_id)
        if existing is not None and not existing.task.done():
            existing.task.cancel()
        _turns[conversation_id] = TurnState(
            task=task,
            turn_id=turn_id,
            conversation_id=conversation_id,
            started_at=started_at,
            global_slot_held=True,
        )


async def unregister(conversation_id: uuid.UUID, turn_id: uuid.UUID | None = None) -> None:
    global_slot_held = False
    async with _lock:
        state = _turns.pop(conversation_id, None)
        if state is not None:
            if turn_id is None:
                turn_id = state.turn_id
            global_slot_held = state.global_slot_held
    if turn_id is not None:
        await asyncio.to_thread(
            chat_turn_redis.clear_active_turn,
            conversation_id,
            turn_id,
        )
        if global_slot_held:
            await asyncio.to_thread(
                chat_turn_slots.release_turn_slot,
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


async def _wait_turn_inactive(conversation_id: uuid.UUID) -> bool:
    deadline = asyncio.get_running_loop().time() + _cancel_wait_timeout()
    while asyncio.get_running_loop().time() < deadline:
        if not await is_turn_active(conversation_id):
            return True
        await asyncio.sleep(CANCEL_WAIT_POLL_INTERVAL)
    logger.warning(
        "chat_turn_registry: cancel wait timed out for conversation %s",
        conversation_id,
    )
    return False


async def cancel_turn(conversation_id: uuid.UUID) -> CancelResult:
    if await cancel_local_turn(conversation_id):
        return CancelResult(cancelled=True, turn_inactive=True)
    if not await is_turn_active(conversation_id):
        return CancelResult(cancelled=False, turn_inactive=True)
    await asyncio.to_thread(chat_turn_redis.publish_cancel, conversation_id)
    turn_inactive = await _wait_turn_inactive(conversation_id)
    return CancelResult(cancelled=True, turn_inactive=turn_inactive)
