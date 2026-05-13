from __future__ import annotations
import requests
import urllib3
import numpy as np
import os
import difflib
from .pydantic_models import (
    AgentMaxRoundsResult,
    AgentParseEmptyResult,
    AgentParseErrorResult,
    AgentParseSuccessResult,
    AgentTextResult,
    AgentToolCallsResult,
    ChatCompletionResponse,
    ToolCallNormalized,
)
from .registry import Messages, Tool
from pydantic import BaseModel
from typing import List, Union, Dict, Any, Type
import json
from pathlib import Path
import tomllib
from dotenv import load_dotenv
import asyncio
from typing import Iterator, AsyncIterator, cast, Optional

SETTINGS = Path(__file__).resolve().parent

load_dotenv()


def _parse_function_arguments(raw: str) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


class Agent:
    def __init__(self, settings_path: str | Path = SETTINGS / "settings.toml"):
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

        self.settings = self._load_settings(settings_path)
        agent_cfg = self.settings.get("agent", {}) or {}

        self.model = agent_cfg.get("model", "") or ""
        self.vector_model = agent_cfg.get("vector", "") or ""
        self.dim = self._to_int(agent_cfg.get("dim"), default=None)

        self._model_ids: list[str] | None = None

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

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            resp = self.session.request(
                method=method,
                url=url,
                json=payload,
                verify=self.verify_ssl,
            )
            if not resp.ok:
                print("Status:", resp.status_code)
                print("Response text:", resp.text)  # 👈 THIS IS KEY
                resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise RuntimeError(f"Request failed for {path}: {e}") from e
        except ValueError as e:
            raise RuntimeError(f"Server returned invalid JSON for {path}") from e

    def _request_stream(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        """
        Stream OpenAI-compatible chat completions.

        Expects SSE lines with `data: {json}` and terminates at `data: [DONE]`.
        Yields decoded JSON objects (one per SSE data event).
        """
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            with self.session.post(url, json=payload, verify=self.verify_ssl, stream=True) as resp:
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
                        # Ignore malformed chunks rather than killing the stream.
                        continue
        except requests.RequestException as e:
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
        if model in self.model_ids:
            return

        suggestion = difflib.get_close_matches(model, self.model_ids, n=1)
        hint = f" Did you mean '{suggestion[0]}'?" if suggestion else ""
        raise ValueError(f"Invalid model: {model}.{hint}")

    def models(self) -> dict[str, Any]:
        models = self._request("GET", "/models")

        try: #openrouter api case for embed
            embed = self._request("GET", "/models?output_modalities=embeddings")
            combined = {
                **models,
                "data": models.get("data", []) + embed.get("data", [])
            }

            return combined

        except Exception:
            return models
        # return self._request("GET", "/models")

    def invoke(
        self,
        messages: Messages,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "medium",
        stream: bool = False,
    ) -> Union[Iterator[dict[str, Any]], AgentToolCallsResult, AgentTextResult]:
        model = self._resolve_model(model, self.model)
        self._validate_model(model)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages.to_json(),
            "stream": bool(stream),
            "temperature": temperature,
            #"reasoning": {"effort": reasoning},
        }
        if tools is not None:
            payload["tools"] = tools

        if stream:
            # Returns a generator of raw SSE JSON events.
            return self._request_stream("/chat/completions", payload)

        response = ChatCompletionResponse.model_validate(
            self._request("POST", "/chat/completions", payload)
        )
        usage = response.usage
        choice = response.choices[0]
        finish_reason = choice.finish_reason
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
        messages: Messages,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "medium",
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
        messages: Messages,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "medium",
        stream: bool = False,
    ) -> Union[AgentToolCallsResult, AgentTextResult]:
        if stream:
            raise RuntimeError("Use `Agent.stream_async(...)` for streaming.")
        return await asyncio.to_thread(self.invoke, messages, model, tools, temperature, reasoning, False)

    async def stream_async(
        self,
        messages: Messages,
        model: str | None = None,
        tools: List[Dict[str, Any]] | None = None,
        temperature: float = 1.2,
        reasoning: str = "medium",
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


    # async def run_async(
    #     self,
    #     messages: Messages,
    #     model: str | None = None,
    #     tools: List[Dict[str, Any]] | None = None,
    #     temperature: float = 1.2,
    #     reasoning: str = "medium",
    #     max_tool_rounds: int = 25,
    #     register_mcp_tools: bool = True,
    # ) -> Union[AgentToolCallsResult, AgentTextResult, AgentMaxRoundsResult]:
    #     """
    #     Execute a full chat loop, including tool calling.

    #     - If `register_mcp_tools` is True, reload local tools and rediscover MCP tools (includes fastmcp discovery).
    #     - Stops when the model returns a non-tool response or max rounds hit.
    #     """
    #     if register_mcp_tools:
    #         await Tool.refresh_tools_async()

    #     rounds = 0
    #     while True:
    #         rounds += 1
    #         if rounds > max_tool_rounds:
    #             return AgentMaxRoundsResult(
    #                 response=f"Stopped after {max_tool_rounds} tool rounds",
    #             )

    #         resp = await self.invoke_async(
    #             messages=messages,
    #             model=model,
    #             tools=tools,
    #             temperature=temperature,
    #             reasoning=reasoning,
    #         )

    #         if not isinstance(resp, AgentToolCallsResult):
    #             return resp

    #         messages.addAssistant(resp.message.model_dump(mode="json"))

    #         for call in resp.tool_calls:
    #             tool_result = await Tool.invoke_async(call)
    #             messages.addTool(call, tool_result)

    def parse(
        self,
        messages: Messages,
        model: str | None = None,
        response_schema: Type[BaseModel] | None = None,
        response_format: Dict[str, Any] | None = None,
        temperature: float = 1.2,
        reasoning: str = "medium",
    ) -> Union[AgentParseEmptyResult, AgentParseSuccessResult, AgentParseErrorResult]:
        model = self._resolve_model(model, self.model)
        self._validate_model(model)

        if response_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
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
            #"reasoning": {"effort": reasoning},
            "response_format": response_format,
        }

        response = ChatCompletionResponse.model_validate(
            self._request("POST", "/chat/completions", payload)
        )
        usage = response.usage
        choice = response.choices[0]
        finish_reason = choice.finish_reason
        msg = choice.message

        reasoning_text = msg.reasoning_content or msg.reasoning or ""
        content = msg.content

        if not content:
            return AgentParseEmptyResult(
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
            )

        try:
            if response_schema is not None:
                parsed = response_schema.model_validate_json(content)
            else:
                parsed = json.loads(content)

            return AgentParseSuccessResult(
                response=parsed,
                reasoning=reasoning_text,
                message=msg,
                usage=usage,
            )
        except (json.JSONDecodeError, ValueError) as e:
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
        messages: Messages,
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
