from __future__ import annotations

import json
from typing import Any, Iterable, List, Optional, Union, Dict

import tiktoken
from pydantic import BaseModel

from nodepoint.agent.schema import (
    AssistantToolCallMessage,
    Message,
    ToolMessage,
    json_safe_for_dump,
)

MessageT = Union[Message, ToolMessage, AssistantToolCallMessage]
_MESSAGE_TYPES = (Message, ToolMessage, AssistantToolCallMessage)


class Thread:

    def __init__(self, system: Optional[str] = None):
        self.thread: List[MessageT] = []
        self.root: Optional["Thread"] = None
        self.parent: Optional["Thread"] = None
        self.branch: List["Thread"] = []
        self.system: Optional[str] = system
        self.list_compression: List[str] = []

        if system is not None:
            self.append(Message(role="system", content=system))

    @property
    def messages(self) -> List[MessageT]:
        return self.thread

    def add_branch(self, context: Optional[str] = None) -> "Thread":
        new = Thread(system=self.system)
        self.branch.append(new)

        new.parent = self
        new.root = self if self.root is None else self.root

        if context is not None:
            new.root.list_compression.append(context)
            new.append(Message(role="user", content=context))
        return new

    def __rshift__(self, context: Optional[str] = None) -> "Thread":
        return self.add_branch(context)

    def to_json(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for msg in self.thread:
            if isinstance(msg, Message):
                result.append({"role": msg.role, "content": msg.content})
            elif isinstance(msg, AssistantToolCallMessage):
                result.append({"role": "assistant", "tool_calls": msg.tool_calls})
            elif isinstance(msg, ToolMessage):
                result.append(
                    {"role": "tool", "tool_call_id": msg.id, "content": msg.content}
                )
        return result

    def count_tokens(self, model: Optional[str] = "gpt-4o-mini") -> int:
        return self._count_tokens_openai_style(self.to_json(), model=model)

    def root_count_tokens(self, model: Optional[str] = "gpt-4o-mini") -> int:
        target = self if self.root is None else self.root
        return target.count_tokens(model=model)

    def _add_to_root(self, message: MessageT) -> None:
        if self.root is not None:
            self.root.append(message)

    def addSystem(self, content: str) -> None:
        message = Message(role="system", content=content)
        self.append(message)
        self.system = content

    def addUser(self, content: str) -> None:
        message = Message(role="user", content=content)
        self.append(message)
        self._add_to_root(message)

    def addAssistant(self, message: Any) -> None:
        if isinstance(message, str):
            payload: Dict[str, Any] = {"content": message}
        elif isinstance(message, dict):
            payload = message
        elif isinstance(message, BaseModel):
            payload = message.model_dump(mode="json")
        else:
            payload = json_safe_for_dump(message) or {}

        tool_calls = payload.get("tool_calls")
        if tool_calls:
            entry = AssistantToolCallMessage(tool_calls=tool_calls)
            self.append(entry)
            self._add_to_root(entry)
            return

        content = payload.get("content") or ""
        entry = Message(role="assistant", content=content)
        self.append(entry)
        self._add_to_root(entry)

    def addTool(self, tool: Any, content: Any = None) -> None:
        if isinstance(content, bytes):
            content = content.decode("utf-8")

        if isinstance(content, str):
            payload = content
        elif isinstance(content, dict):
            payload = json.dumps(content)
        elif isinstance(content, (list, tuple)):
            payload = json.dumps(list(content))
        elif content is not None:
            payload = json.dumps(json_safe_for_dump(content))
        else:
            payload = ""

        entry = ToolMessage(id=tool.id, content=payload)
        self.append(entry)
        self._add_to_root(entry)

    def pop(self, n: int = 1) -> List[MessageT]:
        if n <= 0:
            return []
        popped = self.thread[-n:]
        del self.thread[-n:]
        return popped

    def append(self, message: MessageT) -> "Thread":
        if not isinstance(message, _MESSAGE_TYPES):
            raise TypeError(
                "Argument must be Message, ToolMessage, or AssistantToolCallMessage; "
                f"got {type(message).__name__}"
            )
        self.thread.append(message)
        return self

    def extend(self, messages: Iterable[MessageT]) -> "Thread":
        items = list(messages)
        bad = next((m for m in items if not isinstance(m, _MESSAGE_TYPES)), None)
        if bad is not None:
            raise TypeError(
                "All items must be Message, ToolMessage, or AssistantToolCallMessage; "
                f"got {type(bad).__name__}"
            )
        self.thread.extend(items)
        return self

    def __add__(self, msg: Union[MessageT, Iterable[MessageT]]) -> "Thread":
        if isinstance(msg, _MESSAGE_TYPES):
            return self.append(msg)
        if isinstance(msg, (list, tuple)):
            return self.extend(msg)
        raise TypeError(
            "Argument must be Message, ToolMessage, AssistantToolCallMessage, "
            "or a list of them"
        )

    def __iadd__(self, msg: Union[MessageT, Iterable[MessageT]]) -> "Thread":
        return self.__add__(msg)

    def __len__(self) -> int:
        return len(self.thread)

    def __iter__(self):
        return iter(self.thread)

    def __getitem__(self, idx):
        return self.thread[idx]

    def __repr__(self) -> str:
        return (
            f"Thread(messages={len(self.thread)}, branches={len(self.branch)}, "
            f"has_root={self.root is not None})"
        )

    def _count_tokens_openai_style(
        self,
        thread: List[Dict[str, Any]],
        model: Optional[str],
    ) -> int:
        if model:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
        else:
            encoding = tiktoken.get_encoding("cl100k_base")

        tokens_per_message = 3
        tokens_per_name = 1
        num_tokens = 0
        for message in thread:
            num_tokens += tokens_per_message
            for key, value in message.items():
                if value is None:
                    continue
                if isinstance(value, (dict, list)):
                    s = json.dumps(value, ensure_ascii=False)
                else:
                    s = str(value)
                num_tokens += len(encoding.encode(s))
                if key == "name":
                    num_tokens += tokens_per_name
        num_tokens += 3
        return num_tokens
