from __future__ import annotations

import asyncio
import difflib
import json
from json_repair import repair_json
import logging
import os
import tomllib
import urllib3
import numpy as np
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union, cast
import httpx
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from .schema import *
from nodepoint.registry import Thread, Tool
from nodepoint.settings_loader import settings_path as nodepoint_settings_path

load_dotenv()

logger = logging.getLogger(__name__)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_function_arguments(raw: str) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Streaming parser, accumulation, and agent orchestration
# ---------------------------------------------------------------------------


def tool_calls_to_chat_items(tcn: list[ToolCallNormalized]) -> list[ChatCompletionToolCallItem]:
    """Build OpenAI-shaped tool call items for :class:`AssistantToolCallsMessageEvent`."""
    out: list[ChatCompletionToolCallItem] = []
    for t in tcn:
        arguments = json.dumps(t.args, ensure_ascii=False) if t.args else "{}"
        out.append(
            ChatCompletionToolCallItem(
                id=t.id or "",
                type="function",
                function=ChatCompletionFunction(name=t.name, arguments=arguments),
            )
        )
    return out


def tool_call_items_to_normalized(items: list[ChatCompletionToolCallItem]) -> list[ToolCallNormalized]:
    """Inverse of :func:`tool_calls_to_chat_items` for :meth:`Thread.addTool` / :meth:`Tool.invoke_async`."""
    out: list[ToolCallNormalized] = []
    for item in items:
        out.append(
            ToolCallNormalized(
                name=item.function.name,
                args=_parse_function_arguments(item.function.arguments),
                id=item.id or None,
            )
        )
    return out


def _serialize_tool_result(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(json_safe_for_dump(data), ensure_ascii=False)


@dataclass
class _ToolCallBuffer:
    tool_call_id: str | None = None
    tool_name: str | None = None
    start_sent: bool = False
    end_sent: bool = False
    pending_args: list[str] = field(default_factory=list)

    def merge_and_emit(self) -> list[LLMStreamEvent]:
        out: list[LLMStreamEvent] = []
        if self.tool_call_id and self.tool_name and not self.start_sent:
            out.append(
                ToolCallStartEvent(
                    tool_call_id=self.tool_call_id,
                    tool_name=self.tool_name,
                )
            )
            self.start_sent = True
        if self.start_sent and self.tool_call_id:
            for frag in self.pending_args:
                if frag:
                    out.append(
                        ToolCallDeltaEvent(
                            tool_call_id=self.tool_call_id,
                            arguments_delta=frag,
                        )
                    )
            self.pending_args.clear()
        return out


class ChatCompletionStreamParser:
    """
    Converts OpenAI-style ``chat.completion.chunk`` JSON dicts into :class:`LLMStreamEvent` values.
    """

    def __init__(self) -> None:
        self._tools: dict[tuple[int, int], _ToolCallBuffer] = {}

    def feed_chunk(self, chunk: dict[str, Any]) -> list[LLMStreamEvent]:
        out: list[LLMStreamEvent] = []
        err = chunk.get("error")
        if isinstance(err, dict) and err:
            msg = err.get("message")
            out.append(
                ErrorEvent(message=str(msg) if msg is not None else json.dumps(err, default=str))
            )
            return out
        if isinstance(err, str) and err.strip():
            out.append(ErrorEvent(message=err))
            return out

        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return out

        for ci, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            raw_delta = choice.get("delta")
            if raw_delta is None:
                delta: dict[str, Any] = {}
            elif isinstance(raw_delta, dict):
                delta = raw_delta
            else:
                delta = {}

            for rkey in ("reasoning_content", "reasoning"):
                frag = delta.get(rkey)
                if isinstance(frag, str) and frag:
                    out.append(ThinkingTokenEvent(token=frag))

            content = delta.get("content")
            if isinstance(content, str) and content:
                out.append(AssistantResponseTokenEvent(token=content))

            tclist = delta.get("tool_calls")
            if isinstance(tclist, list):
                for tc in tclist:
                    if not isinstance(tc, dict):
                        continue
                    ti_raw = tc.get("index", 0)
                    try:
                        ti = int(ti_raw)
                    except (TypeError, ValueError):
                        ti = 0
                    key = (ci, ti)
                    buf = self._tools.setdefault(key, _ToolCallBuffer())
                    fn = tc.get("function")
                    if not isinstance(fn, dict):
                        fn = {}
                    tid = tc.get("id")
                    if isinstance(tid, str) and tid.strip():
                        buf.tool_call_id = tid.strip()
                    name = fn.get("name")
                    if isinstance(name, str) and name.strip():
                        buf.tool_name = name.strip()
                    args = fn.get("arguments")
                    if isinstance(args, str) and args:
                        buf.pending_args.append(args)
                    out.extend(buf.merge_and_emit())

            if choice.get("finish_reason") == "tool_calls":
                out.extend(self._end_tools_for_choice(ci))

        return out

    def _end_tools_for_choice(self, ci: int) -> list[LLMStreamEvent]:
        out: list[LLMStreamEvent] = []
        for key, buf in list(self._tools.items()):
            if key[0] != ci:
                continue
            if buf.start_sent and not buf.end_sent and buf.tool_call_id:
                out.append(ToolCallEndEvent(tool_call_id=buf.tool_call_id))
                buf.end_sent = True
        return out

    def close_open_tool_calls(self) -> list[LLMStreamEvent]:
        """If the upstream closed without ``finish_reason``, still emit ``tool_call_end`` for UI consistency."""
        out: list[LLMStreamEvent] = []
        for _, buf in list(self._tools.items()):
            if buf.start_sent and not buf.end_sent and buf.tool_call_id:
                out.append(ToolCallEndEvent(tool_call_id=buf.tool_call_id))
                buf.end_sent = True
        return out


class StreamTurnAccumulator:
    """
    Collects :class:`LLMStreamEvent` / :class:`DoneEvent` slices from :meth:`Agent.stream_events_async` into
    an assistant payload suitable for :meth:`Thread.addAssistant` plus
    :class:`ToolCallNormalized` rows for :meth:`Tool.invoke_async`.
    """

    def __init__(self) -> None:
        self._thinking: list[str] = []
        self._tokens: list[str] = []
        self._tool_ids_ordered: list[str] = []
        self._tools: dict[str, dict[str, Any]] = {}
        self._error: str | None = None

    def feed(self, ev: SingleTurnStreamEvent) -> None:
        if isinstance(ev, ThinkingTokenEvent):
            self._thinking.append(ev.token)
        elif isinstance(ev, AssistantResponseTokenEvent):
            self._tokens.append(ev.token)
        elif isinstance(ev, ToolCallStartEvent):
            buf = self._tools.setdefault(ev.tool_call_id, {"name": "", "args": []})
            buf["name"] = ev.tool_name
            if ev.tool_call_id not in self._tool_ids_ordered:
                self._tool_ids_ordered.append(ev.tool_call_id)
        elif isinstance(ev, ToolCallDeltaEvent):
            self._tools.setdefault(ev.tool_call_id, {"name": "", "args": []})["args"].append(
                ev.arguments_delta
            )
        elif isinstance(ev, ToolCallEndEvent):
            self._tools.setdefault(ev.tool_call_id, {"name": "", "args": []})
        elif isinstance(ev, ErrorEvent):
            self._error = ev.message
        elif isinstance(ev, DoneEvent):
            pass

    def raise_if_error(self) -> None:
        if self._error:
            raise RuntimeError(self._error)

    @property
    def reasoning(self) -> str:
        return "".join(self._thinking)

    @property
    def response_text(self) -> str:
        return "".join(self._tokens)

    def tool_calls_normalized(self) -> list[ToolCallNormalized]:
        out: list[ToolCallNormalized] = []
        for tid in self._tool_ids_ordered:
            info = self._tools.get(tid, {})
            name = str(info.get("name") or "").strip()
            if not name:
                continue
            raw_args = "".join(info.get("args", []))
            out.append(
                ToolCallNormalized(
                    name=name,
                    args=_parse_function_arguments(raw_args),
                    id=tid,
                )
            )
        return out

    def assistant_dict(self) -> dict[str, Any]:
        """Shape compatible with :meth:`Thread.addAssistant` (OpenAI-style assistant fields)."""
        reasoning_text = self.reasoning
        content_text = self.response_text
        tcn = self.tool_calls_normalized()
        if tcn:
            tool_calls: list[dict[str, Any]] = []
            for t in tcn:
                arguments = json.dumps(t.args, ensure_ascii=False) if t.args else "{}"
                tool_calls.append(
                    {
                        "id": t.id or "",
                        "type": "function",
                        "function": {"name": t.name, "arguments": arguments},
                    }
                )
            return {
                "content": content_text or None,
                "tool_calls": tool_calls,
                "reasoning_content": reasoning_text or None,
            }
        return {
            "content": content_text,
            "reasoning_content": reasoning_text or None,
        }


class Agent:
    def __init__(self, settings_path: str | Path | None = None):
        if settings_path is None:
            settings_path = nodepoint_settings_path()
        self.base_url = os.getenv("BASE_URL")

        if not self.base_url:
            raise ValueError("BASE_URL is missing")
        if not os.getenv("API_KEY"):
            raise ValueError("API_KEY is missing")

        self.verify_ssl = False

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {os.getenv('API_KEY')}",
            "Content-Type": "application/json",
        })
        self._async_client: httpx.AsyncClient | None = None

        self.settings = self._load_settings(settings_path)
        agent_cfg = self.settings.get("agent", {}) or {}

        self.model = agent_cfg.get("model", "") or ""
        self.vector_model = agent_cfg.get("vector", "") or ""
        self.dim = self._to_int(agent_cfg.get("dim"), default=None)
        parser_from_env = os.getenv("AGENT_PARSER")
        parser_from_toml = agent_cfg.get("parser", "") or ""
        self.parser_model = (parser_from_env or parser_from_toml or self.model).strip()

        self.skip_model_validation = _env_truthy("AGENT_SKIP_MODEL_VALIDATION", default=False)
        self._model_ids: set[str] | None = None

    def _request_timeout(self) -> tuple[float, float]:
        raw = os.getenv("AGENT_REQUEST_TIMEOUT", "300")
        try:
            read_timeout = float(raw)
        except (TypeError, ValueError):
            read_timeout = 120.0
        return (10.0, read_timeout)

    def _read_timeout_seconds(self) -> float:
        return self._request_timeout()[1]

    def _api_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {os.getenv('API_KEY')}",
            "Content-Type": "application/json",
        }

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            connect_s, read_s = self._request_timeout()
            self._async_client = httpx.AsyncClient(
                headers=self._api_headers(),
                verify=self.verify_ssl,
                timeout=httpx.Timeout(connect_s, read=read_s),
            )
        return self._async_client

    async def aclose(self) -> None:
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None

    def _load_settings(self, settings_path: str | Path) -> dict[str, Any]:
        path = Path(settings_path)
        if not path.exists():
            return {}
        with path.open("rb") as f:
            return tomllib.load(f)

    def _to_int(self, value: Any, default: int | None = None) -> int | None:
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Invalid integer value: {value!r}") from e

    def _resolve_model(self, model: str | None, fallback: str) -> str:
        resolved = model or fallback
        if not resolved:
            raise ValueError("Model is required, but neither argument nor settings provided one")
        return resolved

    def _resolve_dim(self, dim: int | None) -> int | None:
        return self.dim if dim is None else dim

    def _build_chat_payload(
        self,
        messages: Thread,
        model: str,
        *,
        temperature: float,
        reasoning: str | None,
        tools: List[Dict[str, Any]] | None,
        stream: bool,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages.to_json(),
            "stream": bool(stream),
            "temperature": temperature,
        }
        if reasoning is not None:
            payload["reasoning"] = {"effort": reasoning}
        if tools is not None:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _payload_log_context(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        if not payload:
            return {"payload_bytes": 0}
        try:
            payload_bytes = len(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
        except (TypeError, ValueError):
            payload_bytes = -1
        return {
            "model": payload.get("model"),
            "payload_bytes": payload_bytes,
            "message_count": len(payload.get("messages") or []),
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        log_ctx = self._payload_log_context(payload)
        try:
            resp = self.session.request(
                method=method,
                url=url,
                json=payload,
                verify=self.verify_ssl,
                timeout=self._request_timeout(),
            )
            if not resp.ok:
                preview = (resp.text or "")[:2048]
                logger.error(
                    "LLM request failed method=%s path=%s status=%s model=%s "
                    "payload_bytes=%s message_count=%s response_preview=%s",
                    method,
                    path,
                    resp.status_code,
                    log_ctx.get("model"),
                    log_ctx.get("payload_bytes"),
                    log_ctx.get("message_count"),
                    preview,
                )
                resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(
                "LLM request exception method=%s path=%s model=%s payload_bytes=%s error=%s",
                method,
                path,
                log_ctx.get("model"),
                log_ctx.get("payload_bytes"),
                e,
            )
            raise RuntimeError(f"Request failed for {path}: {e}") from e
        except ValueError as e:
            raise RuntimeError(f"Server returned invalid JSON for {path}") from e

    def _request_stream(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            with self.session.post(
                url,
                json=payload,
                verify=self.verify_ssl,
                stream=True,
                timeout=self._request_timeout(),
            ) as resp:
                if not resp.ok:
                    raise RuntimeError(f"Streaming request failed: {resp.status_code} {resp.text}")

                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        yield cast(dict[str, Any], json.loads(data))
                    except json.JSONDecodeError:
                        continue
        except requests.RequestException as e:
            raise RuntimeError(f"Streaming request failed for {path}: {e}") from e

    async def _request_stream_async(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        client = self._get_async_client()
        try:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise RuntimeError(
                        f"Streaming request failed: {resp.status_code} {body.decode(errors='replace')}"
                    )
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        yield cast(dict[str, Any], json.loads(data))
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPError as e:
            raise RuntimeError(f"Streaming request failed for {path}: {e}") from e

    @property
    def model_ids(self) -> set[str]:
        if self._model_ids is None:
            data = self.models().get("data", [])
            self._model_ids = {
                item.get("id")
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        return self._model_ids

    def _validate_model(self, model: str) -> None:
        if self.skip_model_validation:
            return

        try:
            ids = self.model_ids
        except Exception as e:
            configured = {m for m in (self.model, self.parser_model, self.vector_model) if m}
            if model in configured:
                logger.warning(
                    "Model catalog unreachable; allowing configured model %r: %s",
                    model,
                    e,
                )
                return
            raise ValueError(
                f"Cannot validate model {model!r} (failed to fetch /models): {e}"
            ) from e

        if model in ids:
            return

        suggestion = difflib.get_close_matches(model, ids, n=1)
        hint = f" Did you mean '{suggestion[0]}'?" if suggestion else ""
        raise ValueError(f"Invalid model: {model}.{hint}")

    def models(self) -> dict[str, Any]:
        models = self._request("GET", "/models")

        try:
            embed = self._request("GET", "/models?output_modalities=embeddings")
            combined = {
                **models,
                "data": models.get("data", []) + embed.get("data", [])
            }

            return combined

        except Exception:
            return models

    def invoke(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
        stream: bool = False,
    ) -> Union[Iterator[dict[str, Any]], AgentToolCallsResult, AgentTextResult]:
        model = self._resolve_model(model, self.model)
        self._validate_model(model)

        payload = self._build_chat_payload(
            messages,
            model,
            temperature=temperature,
            reasoning=reasoning,
            tools=tools,
            stream=stream,
        )

        if stream:
            return self._request_stream("/chat/completions", payload)

        raw_response = self._request("POST", "/chat/completions", payload)

        if "error" in raw_response:
            raise RuntimeError(
                f"API Error: {raw_response['error']}"
            )

        response = ChatCompletionResponse.model_validate(raw_response)
        usage = response.usage
        choice = response.choices[0]
        finish_reason = choice.finish_reason or "stop"
        msg = choice.message

        reasoning_text = msg.reasoning_content or msg.reasoning or ""
        if finish_reason == "tool_calls":
            raw_calls = msg.tool_calls or []
            tool_calls = [
                ToolCallNormalized(
                    name=tc.function.name,
                    args=_parse_function_arguments(tc.function.arguments),
                    id=tc.id,
                )
                for tc in raw_calls
            ]
            return AgentToolCallsResult(
                tool_calls=tool_calls,
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
            )
        return AgentTextResult(
            finish_reason=finish_reason or "stop",
            response=msg.content or "",
            reasoning=reasoning_text,
            message=msg,
            usage=usage,
        )

    def stream(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
    ) -> Iterator[dict[str, Any]]:
        """
        Convenience wrapper for streaming. Yields raw SSE JSON events.
        """
        return cast(
            Iterator[dict[str, Any]],
            self.invoke(
                messages=messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning=reasoning,
                stream=True,
            ),
        )

    async def invoke_async(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
        stream: bool = False,
    ) -> Union[AgentToolCallsResult, AgentTextResult]:
        if stream:
            raise RuntimeError(
                "Use `Agent.stream_async(...)` for raw SSE chunks, or "
                "`Agent.stream_events_async(...)` / `Agent.stream_agent_events_async(...)` for typed streaming."
            )
        return await asyncio.to_thread(self.invoke, messages, model, tools, temperature, reasoning, False)

    def invoke_compression(
        self,
        messages: Thread,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Union[AgentToolCallsResult, AgentTextResult]:
        """Non-streaming summarization call; omits reasoning by default for gateway compatibility."""
        resolved = self._resolve_model(model, self.model)
        self._validate_model(resolved)
        if max_tokens is None:
            max_tokens = int(os.getenv("CHAT_COMPRESS_MAX_OUTPUT_TOKENS", "2000"))
        reasoning: str | None = None
        if not _env_truthy("CHAT_COMPRESS_OMIT_REASONING", default=True):
            reasoning = "low"
        payload = self._build_chat_payload(
            messages,
            resolved,
            temperature=temperature,
            reasoning=reasoning,
            tools=None,
            stream=False,
            max_tokens=max_tokens,
        )
        raw_response = self._request("POST", "/chat/completions", payload)
        if "error" in raw_response:
            raise RuntimeError(f"API Error: {raw_response['error']}")
        response = ChatCompletionResponse.model_validate(raw_response)
        usage = response.usage
        choice = response.choices[0]
        finish_reason = choice.finish_reason or "stop"
        msg = choice.message
        reasoning_text = msg.reasoning_content or msg.reasoning or ""
        if finish_reason == "tool_calls":
            raw_calls = msg.tool_calls or []
            tool_calls = [
                ToolCallNormalized(
                    name=tc.function.name,
                    args=_parse_function_arguments(tc.function.arguments),
                    id=tc.id,
                )
                for tc in raw_calls
            ]
            return AgentToolCallsResult(
                tool_calls=tool_calls,
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
            )
        return AgentTextResult(
            finish_reason=finish_reason or "stop",
            response=msg.content or "",
            reasoning=reasoning_text,
            message=msg,
            usage=usage,
        )

    async def invoke_compression_async(
        self,
        messages: Thread,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> Union[AgentToolCallsResult, AgentTextResult]:
        return await asyncio.to_thread(
            self.invoke_compression, messages, model, temperature, max_tokens
        )

    async def stream_async(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Async wrapper around streaming generator.

        Note: implemented via a thread so callers can `async for` chunks.
        """
        q: asyncio.Queue[Optional[dict[str, Any]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _worker():
            try:
                for event in self.stream(
                    messages=messages,
                    model=model,
                    tools=tools,
                    temperature=temperature,
                    reasoning=reasoning,
                ):
                    loop.call_soon_threadsafe(q.put_nowait, event)
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        # Run the blocking stream reader in a background thread while we yield from the queue.
        task = asyncio.create_task(asyncio.to_thread(_worker))
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            await task

    async def stream_events_async(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
        *,
        emit_terminal_done: bool = True,
    ) -> AsyncIterator[SingleTurnStreamEvent]:
        """
        Async generator of validated :class:`SingleTurnStreamEvent` models.

        Uses httpx SSE streaming (no dedicated thread per active chat).
        """
        model = self._resolve_model(model, self.model)
        self._validate_model(model)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages.to_json(),
            "stream": True,
            "temperature": temperature,
            "reasoning": {"effort": reasoning},
        }
        if tools is not None:
            payload["tools"] = tools

        parser = ChatCompletionStreamParser()
        had_error = False
        try:
            async for raw in self._request_stream_async("/chat/completions", payload):
                if not isinstance(raw, dict):
                    continue
                for ev in parser.feed_chunk(raw):
                    yield ev
                    if isinstance(ev, ErrorEvent):
                        had_error = True
                if had_error:
                    break
            if not had_error:
                for ev in parser.close_open_tool_calls():
                    yield ev
                if emit_terminal_done:
                    yield DoneEvent()
        except Exception as exc:
            yield ErrorEvent(message=str(exc))

    async def stream_agent_events_async(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
        max_tool_rounds: int = 25,
        register_mcp_tools: bool = True,
    ) -> AsyncIterator[AgentStreamEvent]:
        """
        Multi-round agent event stream (emit-only: does not mutate ``Thread``).

        Canonical ordering per tool round:

        1. :class:`AgentTurnStartEvent`
        2. :class:`ThinkingTokenEvent` / tool-call lifecycle / :class:`AssistantResponseTokenEvent` from the model
        3. :class:`ModelTurnCompleteEvent`
        4. If ``finish_reason`` implies tools: :class:`AssistantToolCallsMessageEvent`, then one
           :class:`ToolResultEvent` per tool (``asyncio.gather`` with ``return_exceptions=True``).
        5. On final text turn: :class:`AgentSessionDoneEvent` after stream events.

        The caller must append assistant and tool messages to ``messages`` between rounds
        (same contract as a manual ``invoke`` / ``Tool.invoke`` loop).
        """
        if register_mcp_tools:
            await Tool.refresh_tools_async()

        rounds = 0
        while True:
            rounds += 1
            if rounds > max_tool_rounds:
                yield ErrorEvent(message=f"Stopped after {max_tool_rounds} tool rounds")
                return

            yield AgentTurnStartEvent(turn_index=rounds - 1)

            acc = StreamTurnAccumulator()
            async for ev in self.stream_events_async(
                messages=messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning=reasoning,
                emit_terminal_done=False,
            ):
                yield ev
                acc.feed(ev)
                if isinstance(ev, ErrorEvent):
                    return

            acc.raise_if_error()

            tcn = acc.tool_calls_normalized()
            finish_reason = "tool_calls" if tcn else "stop"
            yield ModelTurnCompleteEvent(finish_reason=finish_reason)

            if tcn:
                yield AssistantToolCallsMessageEvent(
                    tool_calls=tool_calls_to_chat_items(tcn),
                    reasoning_content=acc.reasoning or None,
                    content=acc.response_text or None,
                )
                results = await asyncio.gather(
                    *[Tool.invoke_async(c) for c in tcn],
                    return_exceptions=True,
                )
                for call, res in zip(tcn, results, strict=True):
                    if isinstance(res, BaseException):
                        yield ToolResultEvent(
                            tool_call_id=call.id or "",
                            tool_name=call.name,
                            result=str(res),
                            ok=False,
                        )
                    else:
                        yield ToolResultEvent(
                            tool_call_id=call.id or "",
                            tool_name=call.name,
                            result=_serialize_tool_result(res),
                            ok=True,
                        )
            else:
                yield AgentSessionDoneEvent()
                return


    async def run_async(
        self,
        messages: Thread,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "low",
        max_tool_rounds: int = 25,
        register_mcp_tools: bool = True,
    ) -> Union[AgentToolCallsResult, AgentTextResult, AgentMaxRoundsResult]:
        """
        Execute a full chat loop, including tool calling.

        - If `register_mcp_tools` is True, reload local tools and rediscover MCP tools (includes fastmcp discovery).
        - Stops when the model returns a non-tool response or max rounds hit.
        """
        if register_mcp_tools:
            await Tool.refresh_tools_async()

        rounds = 0
        while True:
            rounds += 1
            if rounds > max_tool_rounds:
                return AgentMaxRoundsResult(
                    response=f"Stopped after {max_tool_rounds} tool rounds",
                )

            resp = await self.invoke_async(
                messages=messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning=reasoning,
            )

            if not isinstance(resp, AgentToolCallsResult):
                return resp

            messages.addAssistant(resp.message)

            for call in resp.tool_calls:
                tool_result = await Tool.invoke_async(call)
                messages.addTool(call, tool_result)

    def parse(
        self,
        messages: Thread,
        model: str | None = None,
        response_schema: Type[BaseModel] | None = None,
        response_format: Dict[str, Any] | None = None,
        temperature: float = 0.3,
        reasoning: str = "low",
    ) -> Union[AgentParseEmptyResult, AgentParseSuccessResult, AgentParseErrorResult, AgentJsonParseSuccessResult]:
        model = self._resolve_model(model, self.parser_model)
        self._validate_model(model)

        if response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            }
        elif response_format is None:
            response_format = {"type": "json_object"}

        payload = {
            "model": model,
            "messages": messages.to_json(),
            "stream": False,
            "temperature": temperature,
            "response_format": response_format,
            "reasoning": {"effort": reasoning}
        }

        raw_response = self._request("POST", "/chat/completions", payload)

        if "error" in raw_response:
            raise RuntimeError(
                f"API Error: {raw_response['error']}"
            )

        response = ChatCompletionResponse.model_validate(raw_response)
        usage = response.usage
        choice = response.choices[0]
        msg = choice.message
        finish_reason = choice.finish_reason or "stop"

        reasoning_text = msg.reasoning_content or msg.reasoning or ""
        content = msg.content
        
        if finish_reason != "stop":

            if finish_reason == "length":
                return AgentParseErrorResult(
                    reasoning=reasoning_text,
                    message=msg,
                    usage=response.usage,
                    error=(
                        "Generation truncated (finish_reason='length'). "
                        "Increase max_tokens or reduce input size."
                    ),
                )
            
            elif finish_reason == "content_filter":
                return AgentParseErrorResult(
                    reasoning=reasoning_text,
                    message=msg,
                    usage=response.usage,
                    error="Content filtered",
                )
            
            return AgentParseErrorResult(
                reasoning=reasoning_text,
                message=msg,
                usage=response.usage,
                error=f"Incomplete generation. Finish reason: {choice.finish_reason}",
            )
        if not content:
            return AgentParseEmptyResult(
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
            )
        
        content = repair_json(content)
        try:
            if response_schema is not None:
                parsed = response_schema.model_validate_json(content)
                return AgentParseSuccessResult(
                    response=parsed,
                    reasoning=reasoning_text,
                    message=msg,
                    usage=usage,
                )

            parsed = json.loads(content)
            return AgentJsonParseSuccessResult(
                response=parsed,
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
            )

        except Exception as e:
            return AgentParseErrorResult(
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
                error=str(e),
            )

    def _reduce_and_normalize_embeddings(
        self,
        data: list[dict[str, Any]],
        dim: int | None = None,
    ) -> np.ndarray:
        embeddings = np.array([item["embedding"] for item in data], dtype=np.float32)

        if dim is not None:
            if dim > embeddings.shape[1]:
                raise ValueError(f"dim ({dim}) > embedding size ({embeddings.shape[1]})")
            embeddings = embeddings[:, :dim]

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return embeddings / norms

    def vector(
        self,
        docs: str | list[str] | None = None,
        model: str | None = None,
        dim: int | None = None,
    ):
        model = self._resolve_model(model, self.vector_model)
        self._validate_model(model)
        dim = self._resolve_dim(dim)

        if isinstance(docs, str):
            docs = [docs]
        elif docs is None:
            raise ValueError("docs is required")
        elif not all(isinstance(d, str) for d in docs):
            raise TypeError("All docs must be strings")

        if not any(d.strip() for d in docs):
            raise ValueError("docs is empty")

        payload = {
            "model": model,
            "input": docs
        }

        response = self._request("POST", "/embeddings", payload)
        data = response.get("data")
        if not data:
            raise ValueError("No embeddings returned")

        return self._reduce_and_normalize_embeddings(data, dim)

    async def parse_async(
        self,
        messages: Thread,
        model: str | None = None,
        response_schema: Type[BaseModel] | None = None,
        response_format: Dict[str, Any] | None = None,
        temperature: float = 1.2,
        reasoning: str = "medium",
    ):
        return await asyncio.to_thread(
            self.parse,
            messages,
            model,
            response_schema,
            response_format,
            temperature,
            reasoning,
        )
