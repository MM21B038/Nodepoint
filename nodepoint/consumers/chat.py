from __future__ import annotations

import asyncio
import json
import logging
import uuid
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from nodepoint.services import chat_runner, chat_storage, chat_turn_registry, chat_turn_runner
from nodepoint.services.chat_storage import SessionNotFoundError
from nodepoint.services.chat_turn_cancel_listener import bind_event_loop
from nodepoint.services.chat_turn_registry import (
    ChatTurnQueueAborted,
    ChatTurnQueueTimeout,
    TurnAlreadyActive,
)
from nodepoint.services import chat_storage_async as storage_async
from nodepoint.auth.websocket import authenticate_websocket
from nodepoint.auth.visibility import can_access_workspace
from nodepoint.services.owner_scope import (
    OwnerNotAccessibleError,
    OwnerScopeError,
    parse_owner_id_from_query_dict,
)
from nodepoint.services.workspace import (
    AmbiguousWorkspaceError,
    WorkspaceNotFoundError,
    get_workspace_by_name,
)
from nodepoint.services.workspace_group import (
    AmbiguousGroupError,
    GroupNotFoundError,
    get_group_by_name,
    get_or_create_group_chat_workspace,
    list_group_members_summary,
)
from nodepoint.services import workspace_group as group_svc

logger = logging.getLogger(__name__)


def _parse_connect_query(scope: dict) -> tuple[uuid.UUID | None, bool, str | None]:
    raw = scope.get("query_string") or b""
    if isinstance(raw, bytes):
        raw = raw.decode()
    params = parse_qs(raw)
    session_vals = params.get("session_id") or []
    incognito_vals = params.get("incognito") or []
    incognito = any(v.lower() in ("1", "true", "yes") for v in incognito_vals)
    session_id: uuid.UUID | None = None
    if session_vals:
        try:
            session_id = uuid.UUID(session_vals[0])
        except ValueError:
            return None, incognito, "Invalid session_id"
    if incognito and session_id is not None:
        return None, True, "incognito and session_id are mutually exclusive"
    if not incognito and session_id is None:
        return None, False, "session_id or incognito=true is required"
    return session_id, incognito, None


class ChatConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_name: str | None = None
        self.workspace_name: str | None = None
        self.workspace = None
        self.conversation_id: uuid.UUID | None = None
        self.active_branch_id: uuid.UUID | None = None
        self._channel_group: str | None = None
        self._connected = False
        self._streaming = False
        self.persist = True
        self.incognito = False
        self._thread = None
        self._abort_queued_send = False
        self._owner_id: int | None = None

    async def _reject_connect(self, message: str, *, candidates=None, code: int = 4000):
        await self.accept()
        self._connected = True
        payload: dict = {"type": "error", "message": message}
        if candidates is not None:
            payload["candidates"] = candidates
        await self._safe_send_json(payload)
        await self.close(code=code)

    async def connect(self):
        bind_event_loop(asyncio.get_running_loop())

        user, auth_error = await authenticate_websocket(self.scope)
        if auth_error:
            await self.close(code=4401)
            return
        self.scope["user"] = user

        url_kwargs = self.scope["url_route"]["kwargs"]
        group_name = url_kwargs.get("group_name")

        raw_qs = self.scope.get("query_string") or b""
        if isinstance(raw_qs, bytes):
            raw_qs = raw_qs.decode()
        query_params = parse_qs(raw_qs)
        try:
            owner_id = await database_sync_to_async(parse_owner_id_from_query_dict)(
                query_params, actor=user
            )
        except (OwnerScopeError, OwnerNotAccessibleError) as exc:
            await self._reject_connect(str(exc))
            return
        self._owner_id = owner_id

        if group_name:
            try:
                await database_sync_to_async(get_group_by_name)(
                    group_name, actor=user, owner_id=owner_id
                )
            except AmbiguousGroupError as exc:
                await self._reject_connect(str(exc), candidates=exc.candidates)
                return
            except GroupNotFoundError:
                await self.close(code=4004)
                return
            self.group_name = group_name
            workspace = await database_sync_to_async(get_or_create_group_chat_workspace)(
                group_name, actor=user, owner_id=owner_id
            )
            self.workspace_name = None
        else:
            workspace_name = url_kwargs.get("workspace_name")
            if not workspace_name:
                await self.close(code=4000)
                return
            try:
                workspace = await database_sync_to_async(get_workspace_by_name)(
                    workspace_name, actor=user, owner_id=owner_id
                )
            except AmbiguousWorkspaceError as exc:
                await self._reject_connect(str(exc), candidates=exc.candidates)
                return
            except WorkspaceNotFoundError:
                await self.close(code=4004)
                return
            self.workspace_name = workspace.name

        allowed = await database_sync_to_async(can_access_workspace)(user, workspace)
        if not allowed:
            await self.close(code=4401)
            return

        self.workspace = workspace
        session_id, incognito, query_error = _parse_connect_query(self.scope)
        if query_error:
            await self.accept()
            self._connected = True
            await self._safe_send_json({"type": "error", "message": query_error})
            await self.close(code=4000)
            return

        self.incognito = incognito
        self.persist = not incognito

        if incognito:
            self.conversation_id = uuid.uuid4()
            self.active_branch_id = uuid.uuid4()
            self._thread = await asyncio.to_thread(
                chat_storage.build_empty_thread, settings.CHAT_DEFAULT_SYSTEM
            )
        else:
            try:
                conversation = await storage_async.get_session(workspace, session_id)
            except SessionNotFoundError:
                await self.close(code=4004)
                return
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
            "session_id": str(self.conversation_id),
            "conversation_id": str(self.conversation_id),
            "incognito": self.incognito,
            "agent_busy": False,
        }
        if self.active_branch_id is not None:
            ready["active_branch_id"] = str(self.active_branch_id)
        if self.group_name:
            group = await database_sync_to_async(get_group_by_name)(
                self.group_name, actor=user, owner_id=self._owner_id
            )
            ready["group"] = self.group_name
            ready["tag"] = group.tag
            ready["member_count"] = await database_sync_to_async(
                group_svc.get_group_member_count
            )(group)
            ready["members"] = await database_sync_to_async(
                list_group_members_summary
            )(self.group_name, actor=user, owner_id=self._owner_id)
            if group.tag == "workspace":
                ready["workspaces"] = [
                    member["name"] for member in ready["members"]
                ]
        else:
            ready["workspace"] = self.workspace_name

        turn = await chat_turn_registry.get_status(self.conversation_id)
        ready["agent_busy"] = turn.active
        if turn.active:
            self._streaming = True
            if turn.turn_id:
                ready["turn_id"] = str(turn.turn_id)
            if turn.started_at:
                ready["turn_started_at"] = turn.started_at.isoformat()
            ready["reconnect_hint"] = chat_turn_runner.RECONNECT_HINT
        else:
            self._streaming = False

        await self._safe_send_json(ready)

    async def disconnect(self, code):
        self._connected = False
        self._streaming = False
        self._abort_queued_send = True
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
        if self._abort_queued_send:
            self._abort_queued_send = False
            self._streaming = False
            await self._safe_send_json(
                {
                    "type": "chat.cancelled",
                    "message": "Queued message cancelled.",
                }
            )
            return
        result = await chat_turn_registry.cancel_turn(self.conversation_id)
        if not result.cancelled:
            self._streaming = False
            hint = (
                "No active turn."
                if self.incognito
                else "No active turn (refresh chat history if needed)."
            )
            await self._safe_send_json(
                {
                    "type": "chat.cancelled",
                    "message": hint,
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

        if self.incognito:
            if self._thread is None:
                self._thread = await asyncio.to_thread(
                    chat_storage.build_empty_thread, settings.CHAT_DEFAULT_SYSTEM
                )
            thread = self._thread
            thread.addUser(content)
            branch_id = self.active_branch_id
        else:
            if self.active_branch_id is None and self.conversation_id is not None:
                active = await storage_async.get_active_branch(self.conversation_id)
                self.active_branch_id = active.id
            branch_id = self.active_branch_id
            thread, _, _ = await storage_async.load_thread(branch_id)
            thread.addUser(content)
            await storage_async.append_message_visible(
                self.conversation_id,
                branch_id,
                role="user",
                content=content,
            )

        exclude = set(data.get("exclude_servers") or [])
        tools = await asyncio.to_thread(chat_runner.default_tools, exclude)

        self._streaming = True
        self._abort_queued_send = False

        async def on_queued() -> None:
            await self._safe_send_json(
                {
                    "type": "chat.queued",
                    "message": (
                        "Waiting for a free chat slot "
                        f"(max {int(getattr(settings, 'CHAT_MAX_CONCURRENT_TURNS', 8))} "
                        "parallel turns)…"
                    ),
                }
            )

        try:
            turn_id = await chat_turn_runner.start_turn_queued(
                conversation_id=self.conversation_id,
                branch_id=branch_id,
                thread=thread,
                workspace_name=self.workspace_name,
                group_name=self.group_name,
                tools=tools,
                exclude_servers=exclude,
                persist=self.persist,
                on_queued=on_queued,
                should_abort=lambda: self._abort_queued_send,
            )
        except TurnAlreadyActive:
            self._streaming = False
            await self._safe_send_json(
                {
                    "type": "error",
                    "message": "Agent busy — wait for the current turn or send chat.reconnect",
                }
            )
            return
        except ChatTurnQueueAborted:
            self._streaming = False
            return
        except ChatTurnQueueTimeout:
            self._streaming = False
            await self._safe_send_json(
                {
                    "type": "error",
                    "code": "chat_queue_timeout",
                    "message": (
                        "Timed out waiting for a free chat slot. "
                        "Try again shortly."
                    ),
                }
            )
            return
        if self.incognito:
            self._thread = thread
        await self._safe_send_json(
            {"type": "chat.turn_started", "turn_id": str(turn_id)}
        )

    async def _apply_stream_side_effects(self, payload: dict) -> None:
        ev_type = payload.get("type")
        if ev_type == "chat.compressed" and self.persist and self.conversation_id is not None:
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
