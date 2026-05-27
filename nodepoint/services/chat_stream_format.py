from __future__ import annotations

from typing import Any


class ChatStreamFormatter:
    """Turn raw agent events into UI WebSocket frames (section open/close + payload)."""

    def __init__(self) -> None:
        self.thinking_open = False
        self.response_open = False

    def format(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        ev_type = payload.get("type")
        frames: list[dict[str, Any]] = []

        if ev_type == "thinking_token":
            if not self.thinking_open:
                frames.append(
                    {"type": "section", "section": "thinking", "action": "open"}
                )
                self.thinking_open = True
            frames.append(payload)
        elif ev_type == "assistant_response_token":
            if self.thinking_open:
                frames.append(
                    {"type": "section", "section": "thinking", "action": "close"}
                )
                self.thinking_open = False
            if not self.response_open:
                frames.append(
                    {"type": "section", "section": "response", "action": "open"}
                )
                self.response_open = True
            frames.append(payload)
        elif ev_type in ("tool_calls", "assistant_tool_calls_message"):
            frames.extend(self._close_open_sections())
            frames.append(
                {"type": "section", "section": "tool_calls", "action": "open"}
            )
            frames.append(payload)
            frames.append(
                {"type": "section", "section": "tool_calls", "action": "close"}
            )
        elif ev_type == "tool_completed":
            frames.append(
                {"type": "section", "section": "tool_completed", "action": "open"}
            )
            frames.append(payload)
            frames.append(
                {"type": "section", "section": "tool_completed", "action": "close"}
            )
        else:
            frames.append(payload)

        return frames

    def close_sections(self) -> list[dict[str, Any]]:
        return self._close_open_sections()

    def _close_open_sections(self) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        if self.thinking_open:
            frames.append(
                {"type": "section", "section": "thinking", "action": "close"}
            )
            self.thinking_open = False
        if self.response_open:
            frames.append(
                {"type": "section", "section": "response", "action": "close"}
            )
            self.response_open = False
        return frames
