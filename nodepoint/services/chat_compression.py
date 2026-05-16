from __future__ import annotations

from nodepoint.agent.agent import Agent
from nodepoint.agent.schema import AgentTextResult, AgentToolCallsResult
from nodepoint.registry import Prompt, Thread


COMPRESSION_USER_PROMPT = (
    "max-token / window handoff, now generate an report for this overall "
    "conversation till the last message"
)


async def compress_async(agent: Agent, thread: Thread) -> str:
    temp_thread = Thread()
    temp_thread.addSystem(Prompt["context_compression"])
    if len(thread.messages) > 1:
        temp_thread.extend(thread.messages[1:])
    temp_thread.addUser(COMPRESSION_USER_PROMPT)

    resp = await agent.invoke_async(messages=temp_thread)
    if isinstance(resp, AgentTextResult):
        return resp.response or ""
    if isinstance(resp, AgentToolCallsResult):
        return resp.response or ""
    return str(getattr(resp, "response", "") or "")
