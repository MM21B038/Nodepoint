from __future__ import annotations

import logging
import time
from typing import cast
from uuid import UUID

from django.conf import settings
from django_rq import get_connection

logger = logging.getLogger(__name__)

SLOTS_KEY = "nodepoint:chat:parallel_turns"


def _max_parallel_turns() -> int:
    return max(1, int(getattr(settings, "CHAT_MAX_CONCURRENT_TURNS", 8)))


def _slot_ttl() -> int:
    return int(getattr(settings, "CHAT_TURN_REDIS_TTL", 3600))


def _slot_member(conversation_id: UUID, turn_id: UUID) -> str:
    return f"{conversation_id}:{turn_id}"


_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local expiry = tonumber(ARGV[2])
local max_slots = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZSCORE', key, member) then
    return 1
end
if redis.call('ZCARD', key) >= max_slots then
    return 0
end
redis.call('ZADD', key, expiry, member)
return 1
"""

_RELEASE_SCRIPT = """
local key = KEYS[1]
local member = ARGV[1]
return redis.call('ZREM', key, member)
"""


def try_acquire_turn_slot(conversation_id: UUID, turn_id: UUID) -> bool:
    """Reserve a global parallel chat slot (shared across workers)."""
    conn = get_connection()
    now = time.time()
    expiry = now + _slot_ttl()
    member = _slot_member(conversation_id, turn_id)
    result = conn.eval(
        _ACQUIRE_SCRIPT,
        1,
        SLOTS_KEY,
        now,
        expiry,
        _max_parallel_turns(),
        member,
    )
    acquired = bool(result)
    if not acquired:
        logger.info(
            "chat_turn_slots: at capacity (%s) conversation=%s turn=%s",
            _max_parallel_turns(),
            conversation_id,
            turn_id,
        )
    return acquired


def release_turn_slot(conversation_id: UUID, turn_id: UUID) -> bool:
    conn = get_connection()
    member = _slot_member(conversation_id, turn_id)
    removed = conn.eval(_RELEASE_SCRIPT, 1, SLOTS_KEY, member)
    return bool(removed)


def count_active_turn_slots() -> int:
    conn = get_connection()
    now = time.time()
    conn.zremrangebyscore(SLOTS_KEY, "-inf", now)
    return int(cast(int, conn.zcard(SLOTS_KEY)) or 0)


def clear_all_turn_slots() -> None:
    """Remove all global slot entries (tests and admin recovery)."""
    get_connection().delete(SLOTS_KEY)
