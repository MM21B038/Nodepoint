from __future__ import annotations
from pydantic import BaseModel
from typing import List, Union, Literal
from Nodepoint.pydantic_models import json_safe_for_dump
import json

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class AssistantToolCallMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    tool_calls: List
    
class ToolMessage(BaseModel):
    role: Literal["tool"] = "tool"
    id: str
    content: str


class Messages(BaseModel):
    messages: List[Union[Message, ToolMessage, AssistantToolCallMessage]] = []

    def to_json(self):
        result = []
    
        for msg in self.messages:
            if isinstance(msg, Message):
                result.append({
                    "role": msg.role,
                    "content": msg.content
                })
    
            elif isinstance(msg, AssistantToolCallMessage):
                result.append({
                    "role": "assistant",
                    "tool_calls": msg.tool_calls
                })
    
            elif isinstance(msg, ToolMessage):
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.id,
                    "content": msg.content
                })
    
        return result

    def addSystem(self, content):
        self.messages.append(Message(role="system", content=content))
        
    def addUser(self, content):
        self.messages.append(Message(role="user", content=content))
        
    def addAssistant(self, message):
        if isinstance(message, str):
            message = {"content": message}
    
        elif isinstance(message, dict):
            pass
    
        elif isinstance(message, BaseModel):
            message = message.model_dump(mode="json")

        else:
            message = json_safe_for_dump(message)
    
        # Handle tool calls
        tool_calls = message.get("tool_calls")
        if tool_calls:
            self.messages.append(
                AssistantToolCallMessage(tool_calls=tool_calls)
            )
            return
    
        # Handle normal assistant message
        content = message.get("content", "")
    
        if content is None:
            content = ""
    
        self.messages.append(
            Message(
                role="assistant",
                content=content
            )
        )
        
    def addTool(self, tool, content=None):
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        
        if isinstance(content, str):
            payload = content
    
        elif isinstance(content, dict):
            payload = json.dumps(content)
    
        elif isinstance(content, (list, tuple)):
            payload = json.dumps(content)
    
        elif content is not None:
            payload = json.dumps(json_safe_for_dump(content))
    
        else:
            payload = ""
    
        self.messages.append(
            ToolMessage(id=tool.id, content=payload)
        )

    def pop(self, n:int = 1):
        popped = self.messages[-n:]
        del self.messages[-n:]
        return popped