from __future__ import annotations

from typing import Any, Dict, List


class ChatStreamFormatter:
    """Turn raw agent events into UI WebSocket frames (section open/close + payload)."""

    def __init__(self) -> None:
        self.thinking_open: bool = False
        self.response_open: bool = False
        self.compression_open: bool = False

    def format(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        ev_type = payload.get("type")
        frames: List[Dict[str, Any]] = []

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
        elif ev_type == "chat.compress_started":
            frames.extend(self._close_open_sections())
            frames.append(
                {"type": "section", "section": "compression", "action": "open"}
            )
            frames.append(payload)
            self.compression_open = True
        elif ev_type in ("chat.compress_completed", "chat.compress_failed"):
            frames.append(payload)
            if self.compression_open:
                frames.append(
                    {
                        "type": "section",
                        "section": "compression",
                        "action": "close",
                    }
                )
                self.compression_open = False
        elif ev_type == "chat.compressed":
            frames.append(payload)
        else:
            frames.append(payload)

        return frames

    def close_sections(self) -> List[Dict[str, Any]]:
        return self._close_open_sections()

    def _close_open_sections(self) -> List[Dict[str, Any]]:
        frames: List[Dict[str, Any]] = []
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
