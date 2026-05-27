from __future__ import annotations

import json
import logging
import os
from typing import Any

from django.conf import settings

from nodepoint.agent.agent import Agent
from nodepoint.agent.schema import (
    AgentTextResult,
    AgentToolCallsResult,
    AssistantToolCallMessage,
    Message,
    ToolMessage,
)
from nodepoint.registry import Prompt, Thread

logger = logging.getLogger(__name__)

COMPRESSION_USER_PROMPT = (
    "max-token / window handoff, now generate an report for this overall "
    "conversation till the last message"
)

_TRUNCATION_SUFFIX = "\n\n...[truncated for compression]..."


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max_chars - len(_TRUNCATION_SUFFIX)
    if keep < 1:
        return text[:max_chars]
    return text[:keep] + _TRUNCATION_SUFFIX


def _copy_message_slim(
    msg: Any,
    *,
    max_tool_chars: int,
    max_assistant_chars: int,
) -> Any:
    if isinstance(msg, ToolMessage):
        return ToolMessage(
            id=msg.id,
            content=_truncate_text(msg.content or "", max_tool_chars),
        )
    if isinstance(msg, Message):
        if msg.role == "assistant":
            content = _truncate_text(msg.content or "", max_assistant_chars)
        elif msg.role == "user":
            content = msg.content or ""
        else:
            content = msg.content or ""
        return Message(role=msg.role, content=content)
    if isinstance(msg, AssistantToolCallMessage):
        return AssistantToolCallMessage(tool_calls=msg.tool_calls)
    return msg


def build_compression_thread(thread: Thread) -> Thread:
    """Copy conversation for summarization with truncated tool/assistant bodies."""
    max_tool = int(getattr(settings, "CHAT_COMPRESS_MAX_TOOL_CHARS", 4000))
    max_assistant = int(getattr(settings, "CHAT_COMPRESS_MAX_ASSISTANT_CHARS", 8000))
    max_messages = int(getattr(settings, "CHAT_COMPRESS_MAX_MESSAGES", 40))

    source = list(thread.messages[1:]) if len(thread.messages) > 1 else []
    if len(source) > max_messages:
        source = source[-max_messages:]

    temp = Thread()
    temp.addSystem(Prompt["context_compression"])
    slim = [_copy_message_slim(m, max_tool_chars=max_tool, max_assistant_chars=max_assistant) for m in source]
    temp.extend(slim)
    temp.addUser(COMPRESSION_USER_PROMPT)
    return temp


def compression_payload_bytes(thread: Thread) -> int:
    return len(json.dumps(thread.to_json(), ensure_ascii=False).encode("utf-8"))


def compression_diagnostics(thread: Thread) -> dict[str, Any]:
    payload_bytes = compression_payload_bytes(thread)
    return {
        "message_count": len(thread.messages),
        "estimated_tokens": thread.count_tokens(),
        "payload_bytes": payload_bytes,
    }


def _compression_model(agent: Agent) -> str:
    override = (os.getenv("CHAT_COMPRESS_MODEL") or "").strip()
    return override or agent.model


async def compress_async(agent: Agent, thread: Thread) -> str:
    temp_thread = build_compression_thread(thread)
    model = _compression_model(agent)
    diag = compression_diagnostics(temp_thread)
    diag["model"] = model
    logger.info(
        "context_compression_start message_count=%s estimated_tokens=%s payload_bytes=%s model=%s",
        diag["message_count"],
        diag["estimated_tokens"],
        diag["payload_bytes"],
        model,
    )
    if _env_truthy("CHAT_COMPRESS_LOG_PAYLOAD"):
        roles = [getattr(m, "role", type(m).__name__) for m in temp_thread.messages]
        logger.debug("compression_message_roles=%s", roles)

    try:
        resp = await agent.invoke_compression_async(messages=temp_thread, model=model)
    except Exception as exc:
        logger.warning(
            "context_compression_failed model=%s estimated_tokens=%s payload_bytes=%s error=%s",
            model,
            diag["estimated_tokens"],
            diag["payload_bytes"],
            exc,
            exc_info=True,
        )
        raise

    if isinstance(resp, AgentTextResult):
        summary = resp.response or ""
    elif isinstance(resp, AgentToolCallsResult):
        summary = resp.response or ""
    else:
        summary = str(getattr(resp, "response", "") or "")

    logger.info(
        "context_compression_done model=%s summary_chars=%s",
        model,
        len(summary),
    )
    return summary


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
