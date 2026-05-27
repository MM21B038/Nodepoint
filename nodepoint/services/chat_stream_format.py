from __future__ import annotations

from typing import Any


class ChatStreamFormatter:
    """
    Turn raw agent events into ordered WebSocket frames.

    Each thinking / response / tool block gets a monotonic ``segment_index`` so clients
    can keep *all* rounds visible (not replace a single buffer). ``chat.done`` includes
    ``latest_response_segment_index`` for citation / interactive UI on the final answer only.
    """

    _TOKEN_SECTIONS = frozenset({"thinking", "response"})

    def __init__(self) -> None:
        self.segment_index = 0
        self.open_section: str | None = None
        self.turn_index: int | None = None
        self.response_segment_indices: list[int] = []
        self.compression_open = False

    def turn_metadata(self) -> dict[str, Any]:
        latest = (
            self.response_segment_indices[-1]
            if self.response_segment_indices
            else None
        )
        return {
            "response_segment_indices": list(self.response_segment_indices),
            "latest_response_segment_index": latest,
        }

    def _close_current(self, *, is_intermediate: bool | None = None) -> list[dict[str, Any]]:
        if self.open_section is None:
            return []
        idx = self.segment_index
        kind = self.open_section
        close_frame: dict[str, Any] = {
            "type": "section",
            "section": kind,
            "action": "close",
            "segment_index": idx,
        }
        if self.turn_index is not None:
            close_frame["turn_index"] = self.turn_index
        if kind == "response":
            if is_intermediate is not None:
                close_frame["is_intermediate"] = is_intermediate
            self.response_segment_indices.append(idx)
        frames = [close_frame]
        self.segment_index += 1
        self.open_section = None
        return frames

    def _open_section(self, kind: str) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        if self.open_section is not None:
            frames.extend(self._close_current())
        open_frame: dict[str, Any] = {
            "type": "section",
            "section": kind,
            "action": "open",
            "segment_index": self.segment_index,
        }
        if self.turn_index is not None:
            open_frame["turn_index"] = self.turn_index
        if kind == "response":
            open_frame["is_intermediate"] = False
        frames.append(open_frame)
        self.open_section = kind
        return frames

    def _tag_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.open_section not in self._TOKEN_SECTIONS:
            return payload
        tagged = dict(payload)
        tagged["segment_index"] = self.segment_index
        if self.turn_index is not None:
            tagged["turn_index"] = self.turn_index
        return tagged

    def _tool_names(self, payload: dict[str, Any]) -> list[str]:
        names = payload.get("names")
        if isinstance(names, list) and names:
            return [str(n) for n in names]
        tool_calls = payload.get("tool_calls") or []
        out: list[str] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if name:
                out.append(str(name))
        return out

    def format(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        ev_type = payload.get("type")
        frames: list[dict[str, Any]] = []

        if ev_type == "agent_turn_start":
            frames.extend(self._close_current())
            self.turn_index = payload.get("turn_index")
            frames.append(
                {
                    **payload,
                    "segment_index": self.segment_index,
                    "turn_index": self.turn_index,
                }
            )
            return frames

        if ev_type == "thinking_token":
            if self.open_section == "response":
                frames.extend(self._close_current(is_intermediate=False))
            elif self.open_section is not None and self.open_section != "thinking":
                frames.extend(self._close_current())
            if self.open_section != "thinking":
                frames.extend(self._open_section("thinking"))
            frames.append(self._tag_payload(payload))
            return frames

        if ev_type == "assistant_response_token":
            if self.open_section == "thinking":
                frames.extend(self._close_current())
            if self.open_section != "response":
                frames.extend(self._open_section("response"))
            frames.append(self._tag_payload(payload))
            return frames

        if ev_type in ("tool_calls", "assistant_tool_calls_message"):
            # Close streaming tokens; partial answer before tools is intermediate.
            if self.open_section == "response":
                frames.extend(self._close_current(is_intermediate=True))
            elif self.open_section is not None:
                frames.extend(self._close_current())

            content = (payload.get("content") or "").strip()
            if content and ev_type == "assistant_tool_calls_message":
                # Model text that was not streamed token-by-token (or completes the block).
                frames.extend(self._open_section("response"))
                open_idx = self.segment_index
                block: dict[str, Any] = {
                    "type": "assistant_message_block",
                    "segment_index": open_idx,
                    "content": content,
                    "is_intermediate": True,
                }
                if self.turn_index is not None:
                    block["turn_index"] = self.turn_index
                frames.append(block)
                frames.extend(self._close_current(is_intermediate=True))

            frames.extend(self._open_section("tool_calls"))
            tool_frame: dict[str, Any] = {
                "type": "tool_calls",
                "names": self._tool_names(payload),
                "segment_index": self.segment_index,
            }
            if payload.get("tool_calls") is not None:
                tool_frame["tool_calls"] = payload.get("tool_calls")
            if self.turn_index is not None:
                tool_frame["turn_index"] = self.turn_index
            frames.append(tool_frame)
            frames.extend(self._close_current())
            return frames

        if ev_type in ("tool_completed", "tool_result"):
            frames.extend(self._open_section("tool_completed"))
            completed: dict[str, Any] = {
                "type": "tool_completed",
                "tool_name": payload.get("tool_name", ""),
                "tool_call_id": payload.get("tool_call_id", ""),
                "ok": payload.get("ok", True),
                "segment_index": self.segment_index,
            }
            if self.turn_index is not None:
                completed["turn_index"] = self.turn_index
            frames.append(completed)
            frames.extend(self._close_current())
            return frames

        if ev_type == "chat.compress_started":
            if self.open_section == "response":
                frames.extend(self._close_current(is_intermediate=False))
            else:
                frames.extend(self._close_current())
            frames.append(
                {"type": "section", "section": "compression", "action": "open", "segment_index": self.segment_index}
            )
            frames.append({**payload, "segment_index": self.segment_index})
            self.compression_open = True
            return frames

        if ev_type in ("chat.compress_completed", "chat.compress_failed"):
            frames.append({**payload, "segment_index": self.segment_index})
            if self.compression_open:
                frames.append(
                    {
                        "type": "section",
                        "section": "compression",
                        "action": "close",
                        "segment_index": self.segment_index,
                    }
                )
                self.segment_index += 1
                self.compression_open = False
            return frames

        if ev_type == "chat.compressed":
            frames.append(payload)
            return frames

        if ev_type == "model_turn_complete":
            frames.append(
                {
                    **payload,
                    "segment_index": self.segment_index,
                    "turn_index": self.turn_index,
                }
            )
            return frames

        frames.append(payload)
        return frames

    def close_sections(self) -> list[dict[str, Any]]:
        if self.open_section == "response":
            return self._close_current(is_intermediate=False)
        return self._close_current()
