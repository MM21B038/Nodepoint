from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, List, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class AssistantToolCallMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    tool_calls: List[Any]


class ToolMessage(BaseModel):
    role: Literal["tool"] = "tool"
    id: str
    content: str


class ChatCompletionFunction(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    arguments: str = ""


class ChatCompletionToolCallItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: str | None = "function"
    function: ChatCompletionFunction


class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str | None = None
    content: str | None = None
    tool_calls: list[ChatCompletionToolCallItem] | None = None
    reasoning_content: str | None = None
    reasoning: str | None = None


class ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="allow")
    finish_reason: str | None = None
    index: int | None = None
    message: ChatCompletionMessage


class ChatCompletionUsage(BaseModel):
    model_config = ConfigDict(extra="allow")
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage | None = None


class ToolCallNormalized(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None


class AgentToolCallsResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    finish_reason: Literal["tool_calls"] = "tool_calls"
    tool_calls: list[ToolCallNormalized]
    reasoning: str = ""
    message: ChatCompletionMessage
    usage: ChatCompletionUsage | None = None


class AgentTextResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    finish_reason: str
    response: str
    reasoning: str = ""
    message: ChatCompletionMessage
    usage: ChatCompletionUsage | None = None


class AgentMaxRoundsResult(BaseModel):
    finish_reason: Literal["stop"] = "stop"
    response: str


class AgentParseEmptyResult(BaseModel):
    finish_reason: Literal["stop"] = "stop"
    response: str = "Empty response received"
    reasoning: str = ""
    message: ChatCompletionMessage
    usage: ChatCompletionUsage | None = None


class AgentParseSuccessResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    finish_reason: Literal["stop"] = "stop"
    response: Any
    reasoning: str = ""
    message: ChatCompletionMessage
    usage: ChatCompletionUsage | None = None


class AgentParseErrorResult(BaseModel):
    finish_reason: Literal["stop"] = "stop"
    response: str = "Invalid JSON response"
    reasoning: str = ""
    message: ChatCompletionMessage
    usage: ChatCompletionUsage | None = None
    error: str


# ---------------------------------------------------------------------------
# Stream and agent events (SSE / discriminated unions)
# ---------------------------------------------------------------------------


class StreamEventType(StrEnum):
    THINKING_TOKEN = "thinking_token"
    ASSISTANT_RESPONSE_TOKEN = "assistant_response_token"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"
    AGENT_TURN_START = "agent_turn_start"
    MODEL_TURN_COMPLETE = "model_turn_complete"
    ASSISTANT_TOOL_CALLS_MESSAGE = "assistant_tool_calls_message"
    AGENT_SESSION_DONE = "agent_session_done"


class BaseStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThinkingTokenEvent(BaseStreamEvent):
    """Reasoning / chain-of-thought delta from ``delta.reasoning*`` fields."""

    type: Literal[StreamEventType.THINKING_TOKEN] = StreamEventType.THINKING_TOKEN
    token: str


class AssistantResponseTokenEvent(BaseStreamEvent):
    """User-visible assistant text from ``delta.content``."""

    type: Literal[StreamEventType.ASSISTANT_RESPONSE_TOKEN] = StreamEventType.ASSISTANT_RESPONSE_TOKEN
    token: str


class ToolCallStartEvent(BaseStreamEvent):
    type: Literal[StreamEventType.TOOL_CALL_START] = StreamEventType.TOOL_CALL_START
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)


class ToolCallDeltaEvent(BaseStreamEvent):
    type: Literal[StreamEventType.TOOL_CALL_DELTA] = StreamEventType.TOOL_CALL_DELTA
    tool_call_id: str = Field(min_length=1)
    arguments_delta: str


class ToolCallEndEvent(BaseStreamEvent):
    type: Literal[StreamEventType.TOOL_CALL_END] = StreamEventType.TOOL_CALL_END
    tool_call_id: str = Field(min_length=1)


class ToolResultEvent(BaseStreamEvent):
    """Emitted after a tool finishes (agent orchestration). ``result`` is always a string."""

    type: Literal[StreamEventType.TOOL_RESULT] = StreamEventType.TOOL_RESULT
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    result: str
    ok: bool | None = None


class ErrorEvent(BaseStreamEvent):
    type: Literal[StreamEventType.ERROR] = StreamEventType.ERROR
    message: str


class DoneEvent(BaseStreamEvent):
    """End of a single HTTP completion stream (``stream_events_async``)."""

    type: Literal[StreamEventType.DONE] = StreamEventType.DONE


class AgentTurnStartEvent(BaseStreamEvent):
    """Begins one model round inside ``stream_agent_events_async``. ``turn_index`` is 0-based."""

    type: Literal[StreamEventType.AGENT_TURN_START] = StreamEventType.AGENT_TURN_START
    turn_index: int = Field(ge=0)


class ModelTurnCompleteEvent(BaseStreamEvent):
    """Model HTTP stream finished for this round (replaces overloading ``DoneEvent`` in agent flows)."""

    type: Literal[StreamEventType.MODEL_TURN_COMPLETE] = StreamEventType.MODEL_TURN_COMPLETE
    finish_reason: str


class AssistantToolCallsMessageEvent(BaseStreamEvent):
    """Full structured assistant tool-call message after the model stream (before tool execution)."""

    type: Literal[StreamEventType.ASSISTANT_TOOL_CALLS_MESSAGE] = (
        StreamEventType.ASSISTANT_TOOL_CALLS_MESSAGE
    )
    tool_calls: list[ChatCompletionToolCallItem]
    reasoning_content: str | None = None
    content: str | None = None


class AgentSessionDoneEvent(BaseStreamEvent):
    """Agent loop ended on a final non-tool assistant response."""

    type: Literal[StreamEventType.AGENT_SESSION_DONE] = StreamEventType.AGENT_SESSION_DONE


LLMStreamEvent = (
    ThinkingTokenEvent
    | AssistantResponseTokenEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | ErrorEvent
)

SingleTurnStreamEvent = LLMStreamEvent | DoneEvent

AgentStreamEvent = (
    LLMStreamEvent
    | AgentTurnStartEvent
    | ModelTurnCompleteEvent
    | AssistantToolCallsMessageEvent
    | ToolResultEvent
    | AgentSessionDoneEvent
)


def to_sse(event: SingleTurnStreamEvent) -> dict[str, str]:
    """Map a single-turn stream event to ``sse-starlette.EventSourceResponse`` payloads."""
    match event:
        case ThinkingTokenEvent():
            return {"event": StreamEventType.THINKING_TOKEN.value, "data": event.model_dump_json()}
        case AssistantResponseTokenEvent():
            return {
                "event": StreamEventType.ASSISTANT_RESPONSE_TOKEN.value,
                "data": event.model_dump_json(),
            }
        case ToolCallStartEvent():
            return {"event": StreamEventType.TOOL_CALL_START.value, "data": event.model_dump_json()}
        case ToolCallDeltaEvent():
            return {"event": StreamEventType.TOOL_CALL_DELTA.value, "data": event.model_dump_json()}
        case ToolCallEndEvent():
            return {"event": StreamEventType.TOOL_CALL_END.value, "data": event.model_dump_json()}
        case ErrorEvent():
            return {"event": StreamEventType.ERROR.value, "data": event.model_dump_json()}
        case DoneEvent():
            return {"event": StreamEventType.DONE.value, "data": event.model_dump_json()}


def to_sse_agent(event: AgentStreamEvent) -> dict[str, str]:
    """SSE mapping for the wider agent stream (includes orchestration envelopes)."""
    match event:
        case ThinkingTokenEvent():
            return {"event": StreamEventType.THINKING_TOKEN.value, "data": event.model_dump_json()}
        case AssistantResponseTokenEvent():
            return {
                "event": StreamEventType.ASSISTANT_RESPONSE_TOKEN.value,
                "data": event.model_dump_json(),
            }
        case ToolCallStartEvent():
            return {"event": StreamEventType.TOOL_CALL_START.value, "data": event.model_dump_json()}
        case ToolCallDeltaEvent():
            return {"event": StreamEventType.TOOL_CALL_DELTA.value, "data": event.model_dump_json()}
        case ToolCallEndEvent():
            return {"event": StreamEventType.TOOL_CALL_END.value, "data": event.model_dump_json()}
        case ToolResultEvent():
            return {"event": StreamEventType.TOOL_RESULT.value, "data": event.model_dump_json()}
        case ErrorEvent():
            return {"event": StreamEventType.ERROR.value, "data": event.model_dump_json()}
        case AgentTurnStartEvent():
            return {"event": StreamEventType.AGENT_TURN_START.value, "data": event.model_dump_json()}
        case ModelTurnCompleteEvent():
            return {
                "event": StreamEventType.MODEL_TURN_COMPLETE.value,
                "data": event.model_dump_json(),
            }
        case AssistantToolCallsMessageEvent():
            return {
                "event": StreamEventType.ASSISTANT_TOOL_CALLS_MESSAGE.value,
                "data": event.model_dump_json(),
            }
        case AgentSessionDoneEvent():
            return {
                "event": StreamEventType.AGENT_SESSION_DONE.value,
                "data": event.model_dump_json(),
            }


def json_safe_for_dump(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {str(k): json_safe_for_dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe_for_dump(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump(mode="json")  # type: ignore[no-any-return]
    if hasattr(obj, "__dict__"):
        return {
            k: json_safe_for_dump(v)
            for k, v in vars(obj).items()
            if not str(k).startswith("_")
        }
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def tool_args_as_dict(args: Any) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, BaseModel):
        return args.model_dump()
    if hasattr(args, "to_dict") and callable(args.to_dict):
        return dict(args.to_dict())
    return dict(args)
