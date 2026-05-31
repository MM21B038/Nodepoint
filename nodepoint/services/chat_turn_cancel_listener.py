from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from nodepoint.services import chat_turn_redis
from nodepoint.services.chat_turn_registry import cancel_local_turn

logger = logging.getLogger(__name__)

_listener_started = False
_listener_lock = threading.Lock()
_main_loop: asyncio.AbstractEventLoop | None = None


def bind_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def _cancel_listener_loop() -> None:
    from django_rq import get_connection

    conn = get_connection()
    pubsub = conn.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(chat_turn_redis.CANCEL_CHANNEL)
    logger.info(
        "chat_turn_cancel_listener: subscribed to %s",
        chat_turn_redis.CANCEL_CHANNEL,
    )
    for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        data = message.get("data")
        if data is None:
            continue
        if isinstance(data, bytes):
            data = data.decode()
        try:
            conversation_id = uuid.UUID(str(data))
        except ValueError:
            logger.warning("chat_turn_cancel_listener: invalid payload %r", data)
            continue
        loop = _main_loop
        if loop is None or not loop.is_running():
            logger.warning(
                "chat_turn_cancel_listener: no event loop for conversation %s",
                conversation_id,
            )
            continue
        future = asyncio.run_coroutine_threadsafe(
            cancel_local_turn(conversation_id),
            loop,
        )
        try:
            future.result(timeout=30)
        except Exception:
            logger.exception(
                "chat_turn_cancel_listener: cancel failed for %s",
                conversation_id,
            )


def start_chat_turn_cancel_listener() -> None:
    global _listener_started
    with _listener_lock:
        if _listener_started:
            return
        thread = threading.Thread(
            target=_cancel_listener_loop,
            name="chat-turn-cancel-listener",
            daemon=True,
        )
        thread.start()
        _listener_started = True
        logger.info("chat_turn_cancel_listener: background thread started")


def should_start_chat_cancel_listener(argv: list[str] | None = None) -> bool:
    """True for uvicorn/web ASGI processes; false for one-shot manage.py commands."""
    import sys

    argv = argv if argv is not None else sys.argv
    if not argv:
        return False
    if argv[0].endswith("manage.py"):
        if len(argv) < 2:
            return False
        command = argv[1]
        if command in {
            "test",
            "migrate",
            "makemigrations",
            "shell",
            "collectstatic",
            "recover_preprocess",
            "rqworker",
            "rqworker-pool",
        }:
            return False
        return command in {"runserver"}
    if "uvicorn" in argv[0] or (len(argv) > 1 and "uvicorn" in argv):
        return True
    return False
