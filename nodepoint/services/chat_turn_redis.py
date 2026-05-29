from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django_rq import get_connection

logger = logging.getLogger(__name__)

TURN_KEY_PREFIX = "nodepoint:chat:turn:"
CANCEL_CHANNEL = "nodepoint:chat:turn:cancel"


def _turn_ttl() -> int:
    return int(getattr(settings, "CHAT_TURN_REDIS_TTL", 3600))


def _turn_key(conversation_id: UUID) -> str:
    return f"{TURN_KEY_PREFIX}{conversation_id}"


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class ActiveTurnRecord:
    turn_id: UUID
    started_at: datetime
    worker_id: str


def _parse_record(raw: str) -> ActiveTurnRecord | None:
    try:
        data = json.loads(raw)
        started_raw = data.get("started_at")
        turn_raw = data.get("turn_id")
        if not started_raw or not turn_raw:
            return None
        started = datetime.fromisoformat(str(started_raw))
        if started.tzinfo is None:
            from datetime import timezone

            started = started.replace(tzinfo=timezone.utc)
        return ActiveTurnRecord(
            turn_id=UUID(str(turn_raw)),
            started_at=started,
            worker_id=str(data.get("worker_id") or ""),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("chat_turn_redis: invalid turn record %r", raw)
        return None


def _serialize_record(
    *,
    turn_id: UUID,
    started_at: datetime,
    owner_worker_id: str,
) -> str:
    payload: dict[str, Any] = {
        "turn_id": str(turn_id),
        "started_at": started_at.isoformat(),
        "worker_id": owner_worker_id,
    }
    return json.dumps(payload, separators=(",", ":"))


def try_set_active_turn(
    conversation_id: UUID,
    turn_id: UUID,
    started_at: datetime,
    *,
    owner_worker_id: str | None = None,
) -> bool:
    """SET NX active turn metadata. Returns False if a turn is already active."""
    conn = get_connection()
    return bool(
        conn.set(
            _turn_key(conversation_id),
            _serialize_record(
                turn_id=turn_id,
                started_at=started_at,
                owner_worker_id=owner_worker_id or worker_id(),
            ),
            nx=True,
            ex=_turn_ttl(),
        )
    )


def get_active_turn(conversation_id: UUID) -> ActiveTurnRecord | None:
    conn = get_connection()
    raw = conn.get(_turn_key(conversation_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return _parse_record(str(raw))


def clear_active_turn(conversation_id: UUID, turn_id: UUID) -> bool:
    """Delete turn key only when turn_id matches (avoids clearing a newer turn)."""
    conn = get_connection()
    key = _turn_key(conversation_id)
    raw = conn.get(key)
    if raw is None:
        return False
    if isinstance(raw, bytes):
        raw = raw.decode()
    record = _parse_record(str(raw))
    if record is None or record.turn_id != turn_id:
        return False
    conn.delete(key)
    return True


def publish_cancel(conversation_id: UUID) -> None:
    conn = get_connection()
    conn.publish(CANCEL_CHANNEL, str(conversation_id))
    logger.debug("chat_turn_redis: published cancel for conversation %s", conversation_id)
