from __future__ import annotations

import asyncio
import json
import logging
import uuid

from channels.generic.websocket import AsyncWebsocketConsumer

from nodepoint.agent.agent import Agent
from nodepoint.services import chat_runner, chat_storage_async as storage_async
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
        self._agent_task: asyncio.Task | None = None
        self._agent: Agent | None = None

    async def connect(self):
        url_kwargs = self.scope["url_route"]["kwargs"]
        group_name = url_kwargs.get("group_name")

        if group_name:
            try:
                await asyncio.to_thread(get_group_by_name, group_name)
            except GroupNotFoundError:
                await self.close(code=4004)
                return
            self.group_name = group_name
            workspace = await asyncio.to_thread(
                get_or_create_group_chat_workspace, group_name
            )
            self.workspace_name = None
        else:
            workspace_name = url_kwargs.get("workspace_name")
            if not workspace_name:
                await self.close(code=4000)
                return
            workspace = await asyncio.to_thread(
                resolve_workspace_for_chat, workspace_name
            )
            if workspace is None:
                await self.close(code=4004)
                return
            self.workspace_name = workspace.name

        conversation, _root = await storage_async.get_or_create_workspace_chat(workspace)
        active = await storage_async.get_active_branch(conversation.id)

        self.conversation_id = conversation.id
        self.active_branch_id = active.id

        await self.accept()
        if self.group_name:
            await self.send_json(
                {
                    "type": "chat.ready",
                    "group": self.group_name,
                    "workspaces": await asyncio.to_thread(
                        list_group_workspace_names, self.group_name
                    ),
                }
            )
        else:
            await self.send_json(
                {
                    "type": "chat.ready",
                    "workspace": self.workspace_name,
                }
            )

    async def disconnect(self, code):
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        if self._agent is not None:
            await self._agent.aclose()
            self._agent = None

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_json({"type": "error", "message": "Invalid JSON"})
            return

        msg_type = data.get("type")
        if msg_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if msg_type == "chat.cancel":
            await self._cancel_agent_task()
            return
        if msg_type == "chat.send":
            await self._handle_send(data)
            return
        await self.send_json({"type": "error", "message": f"Unknown type: {msg_type}"})

    async def _cancel_agent_task(self):
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
            try:
                await self._agent_task
            except asyncio.CancelledError:
                pass
        self._agent_task = None
        await self.send_json({"type": "chat.cancelled"})

    async def _handle_send(self, data: dict):
        if self._agent_task and not self._agent_task.done():
            await self.send_json({"type": "error", "message": "Agent busy"})
            return

        content = (data.get("content") or "").strip()
        if not content:
            await self.send_json({"type": "error", "message": "content is required"})
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

        self._agent_task = asyncio.create_task(
            self._run_agent(thread, branch_id, tools, exclude)
        )

    async def _run_agent(self, thread, branch_id: uuid.UUID, tools: list, exclude: set[str]):
        try:
            if self._agent is None:
                self._agent = Agent()

            thinking_open = False
            response_open = False

            async def on_event(payload: dict):
                nonlocal thinking_open, response_open
                ev_type = payload.get("type")

                if ev_type == "thinking_token":
                    if not thinking_open:
                        await self.send_json(
                            {"type": "section", "section": "thinking", "action": "open"}
                        )
                        thinking_open = True
                    await self.send_json(payload)
                elif ev_type == "assistant_response_token":
                    if thinking_open:
                        await self.send_json(
                            {"type": "section", "section": "thinking", "action": "close"}
                        )
                        thinking_open = False
                    if not response_open:
                        await self.send_json(
                            {"type": "section", "section": "response", "action": "open"}
                        )
                        response_open = True
                    await self.send_json(payload)
                elif ev_type in ("tool_calls", "assistant_tool_calls_message"):
                    if thinking_open:
                        await self.send_json(
                            {"type": "section", "section": "thinking", "action": "close"}
                        )
                        thinking_open = False
                    if response_open:
                        await self.send_json(
                            {"type": "section", "section": "response", "action": "close"}
                        )
                        response_open = False
                    await self.send_json(
                        {
                            "type": "section",
                            "section": "tool_calls",
                            "action": "open",
                        }
                    )
                    await self.send_json(payload)
                    await self.send_json(
                        {
                            "type": "section",
                            "section": "tool_calls",
                            "action": "close",
                        }
                    )
                elif ev_type == "tool_completed":
                    await self.send_json(
                        {
                            "type": "section",
                            "section": "tool_completed",
                            "action": "open",
                        }
                    )
                    await self.send_json(payload)
                    await self.send_json(
                        {
                            "type": "section",
                            "section": "tool_completed",
                            "action": "close",
                        }
                    )
                else:
                    await self.send_json(payload)

            _, new_branch_id = await chat_runner.run_agent_stream(
                thread,
                self._agent,
                branch_id,
                self.conversation_id,
                workspace_name=self.workspace_name,
                group_name=self.group_name,
                tools=tools,
                exclude_servers=exclude,
                on_event=on_event,
            )
            if new_branch_id:
                self.active_branch_id = new_branch_id

            if thinking_open:
                await self.send_json(
                    {"type": "section", "section": "thinking", "action": "close"}
                )
            if response_open:
                await self.send_json(
                    {"type": "section", "section": "response", "action": "close"}
                )
            await self.send_json({"type": "chat.done"})
        except asyncio.CancelledError:
            await self.send_json({"type": "chat.cancelled"})
        except Exception as exc:
            logger.exception(
                "WebSocket agent run failed (conversation=%s group=%s workspace=%s)",
                self.conversation_id,
                self.group_name,
                self.workspace_name,
            )
            await self.send_json({"type": "error", "message": str(exc)})
        finally:
            self._agent_task = None

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content, default=str))
