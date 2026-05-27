from __future__ import annotations

import asyncio
import json
import logging
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from nodepoint.services import chat_runner, chat_turn_registry, chat_turn_runner
from nodepoint.services import chat_storage_async as storage_async
from nodepoint.services.workspace import resolve_workspace_for_chat
from nodepoint.services.workspace_group import (
    GroupNotFoundError,
    get_group_by_name,
    get_or_create_group_chat_workspace,
    list_group_workspace_names,
)

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_name: str | None = None
        self.workspace_name: str | None = None
        self.conversation_id: uuid.UUID | None = None
        self.active_branch_id: uuid.UUID | None = None
        self._channel_group: str | None = None
        self._connected = False
        self._streaming = False

    async def connect(self):
        url_kwargs = self.scope["url_route"]["kwargs"]
        group_name = url_kwargs.get("group_name")

        if group_name:
            try:
                await database_sync_to_async(get_group_by_name)(group_name)
            except GroupNotFoundError:
                await self.close(code=4004)
                return
            self.group_name = group_name
            workspace = await database_sync_to_async(get_or_create_group_chat_workspace)(
                group_name
            )
            self.workspace_name = None
        else:
            workspace_name = url_kwargs.get("workspace_name")
            if not workspace_name:
                await self.close(code=4000)
                return
            workspace = await database_sync_to_async(resolve_workspace_for_chat)(
                workspace_name
            )
            if workspace is None:
                await self.close(code=4004)
                return
            self.workspace_name = workspace.name

        conversation, _root = await storage_async.get_or_create_workspace_chat(workspace)
        active = await storage_async.get_active_branch(conversation.id)

        self.conversation_id = conversation.id
        self.active_branch_id = active.id
        self._channel_group = chat_turn_runner.conversation_channel_group(
            self.conversation_id
        )

        if self.channel_layer is not None:
            await self.channel_layer.group_add(self._channel_group, self.channel_name)

        await self.accept()
        self._connected = True

        ready: dict = {
            "type": "chat.ready",
            "conversation_id": str(self.conversation_id),
            "active_branch_id": str(self.active_branch_id),
        }
        if self.group_name:
            ready["group"] = self.group_name
            ready["workspaces"] = await database_sync_to_async(
                list_group_workspace_names
            )(self.group_name)
        else:
            ready["workspace"] = self.workspace_name

        turn = await chat_turn_registry.get_status(self.conversation_id)
        if turn.active:
            ready["agent_busy"] = True
            self._streaming = True
            if turn.turn_id:
                ready["turn_id"] = str(turn.turn_id)
            if turn.started_at:
                ready["turn_started_at"] = turn.started_at.isoformat()
            ready["reconnect_hint"] = chat_turn_runner.RECONNECT_HINT

        await self._safe_send_json(ready)

    async def disconnect(self, code):
        self._connected = False
        self._streaming = False
        if self._channel_group is not None and self.channel_layer is not None:
            await self.channel_layer.group_discard(
                self._channel_group, self.channel_name
            )

    async def chat_stream(self, event: dict):
        """Channel layer fan-out: live stream for all subscribers on this conversation."""
        if not self._streaming:
            return
        payload = event.get("event")
        if not payload:
            return
        await self._safe_send_json(payload)
        await self._apply_stream_side_effects(payload)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self._safe_send_json({"type": "error", "message": "Invalid JSON"})
            return

        msg_type = data.get("type")
        if msg_type == "ping":
            await self._safe_send_json({"type": "pong"})
            return
        if msg_type == "chat.status":
            await self._send_turn_status()
            return
        if msg_type == "chat.reconnect":
            await self._handle_reconnect()
            return
        if msg_type == "chat.cancel":
            await self._cancel_agent_task()
            return
        if msg_type == "chat.send":
            await self._handle_send(data)
            return
        await self._safe_send_json(
            {"type": "error", "message": f"Unknown type: {msg_type}"}
        )

    async def _handle_reconnect(self):
        if self.conversation_id is None:
            await self._safe_send_json(
                {"type": "chat.reconnected", "agent_busy": False}
            )
            return
        turn = await chat_turn_registry.get_status(self.conversation_id)
        if turn.active:
            self._streaming = True
            payload: dict = {
                "type": "chat.reconnected",
                "agent_busy": True,
                "hint": chat_turn_runner.RECONNECT_HINT,
            }
            if turn.turn_id:
                payload["turn_id"] = str(turn.turn_id)
            if turn.started_at:
                payload["turn_started_at"] = turn.started_at.isoformat()
            await self._safe_send_json(payload)
        else:
            self._streaming = False
            await self._safe_send_json(
                {"type": "chat.reconnected", "agent_busy": False}
            )

    async def _send_turn_status(self):
        if self.conversation_id is None:
            await self._safe_send_json({"type": "chat.status", "agent_busy": False})
            return
        turn = await chat_turn_registry.get_status(self.conversation_id)
        payload: dict = {
            "type": "chat.status",
            "agent_busy": turn.active,
            "active_branch_id": str(self.active_branch_id)
            if self.active_branch_id
            else None,
        }
        if turn.turn_id:
            payload["turn_id"] = str(turn.turn_id)
        if turn.started_at:
            payload["turn_started_at"] = turn.started_at.isoformat()
        await self._safe_send_json(payload)

    async def _cancel_agent_task(self):
        if self.conversation_id is None:
            await self._safe_send_json({"type": "chat.cancelled"})
            return
        cancelled = await chat_turn_registry.cancel_turn(self.conversation_id)
        self._streaming = False
        if cancelled:
            await self._safe_send_json({"type": "chat.cancelled"})
        else:
            await self._safe_send_json(
                {
                    "type": "chat.cancelled",
                    "message": "No active turn (refresh chat history if needed).",
                }
            )

    async def _handle_send(self, data: dict):
        if self.conversation_id is not None and await chat_turn_registry.is_turn_active(
            self.conversation_id
        ):
            await self._safe_send_json(
                {
                    "type": "error",
                    "message": "Agent busy — wait for the current turn or send chat.reconnect",
                }
            )
            return

        content = (data.get("content") or "").strip()
        if not content:
            await self._safe_send_json(
                {"type": "error", "message": "content is required"}
            )
            return

        if self.active_branch_id is None and self.conversation_id is not None:
            active = await storage_async.get_active_branch(self.conversation_id)
            self.active_branch_id = active.id

        branch_id = self.active_branch_id
        thread, _, _ = await storage_async.load_thread(branch_id)
        thread.addUser(content)
        await storage_async.append_message(
            branch_id,
            role="user",
            content=content,
        )

        exclude = set(data.get("exclude_servers") or [])
        tools = await asyncio.to_thread(chat_runner.default_tools, exclude)

        self._streaming = True
        turn_id = await chat_turn_runner.start_turn(
            conversation_id=self.conversation_id,
            branch_id=branch_id,
            thread=thread,
            workspace_name=self.workspace_name,
            group_name=self.group_name,
            tools=tools,
            exclude_servers=exclude,
        )
        await self._safe_send_json(
            {"type": "chat.turn_started", "turn_id": str(turn_id)}
        )

    async def _apply_stream_side_effects(self, payload: dict) -> None:
        ev_type = payload.get("type")
        if ev_type == "chat.compressed" and self.conversation_id is not None:
            active = await storage_async.get_active_branch(self.conversation_id)
            self.active_branch_id = active.id
            await self._safe_send_json(
                {
                    "type": "chat.branch_updated",
                    "active_branch_id": str(active.id),
                }
            )
        elif ev_type == "chat.done":
            self._streaming = False
            branch_raw = payload.get("active_branch_id")
            if branch_raw:
                self.active_branch_id = uuid.UUID(str(branch_raw))
        elif ev_type in ("chat.cancelled", "error"):
            self._streaming = False

    async def _safe_send_json(self, content: dict) -> None:
        if not self._connected:
            return
        try:
            await self.send(text_data=json.dumps(content, default=str))
        except Exception:
            self._connected = False
            logger.debug(
                "WebSocket send failed (conversation=%s); client likely disconnected",
                self.conversation_id,
            )
