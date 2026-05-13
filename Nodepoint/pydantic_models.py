from __future__ import annotations
import json
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


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


def json_safe_for_dump(obj: Any) -> Any:
    """
    Turn nested values into JSON-serializable structures (for json.dumps).
    Replaces the old ObjectToDict helper for Pydantic-era code paths.
    """
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
    """Support dict, Pydantic model, or legacy .to_dict() on tool args."""
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, BaseModel):
        return args.model_dump()
    if hasattr(args, "to_dict") and callable(args.to_dict):
        return dict(args.to_dict())
    return dict(args)
