"""The agent loop, with tools supplied at runtime by MCP servers.

Same loop as Day 1 (ask model -> run tools -> send results -> repeat), with
two differences:
- the tool list comes from whatever MCP servers are connected, not from code;
- tools that MCP marks as possibly destructive need a human "yes" first.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import anyio

SYSTEM_PROMPT = (
    "You are a concise assistant in a terminal. Your tools come from MCP servers; "
    "each tool name starts with its server name, e.g. notes__search_notes. "
    "Use tools instead of guessing, never invent note ids, and keep answers short."
)

DECLINED = "ERROR: the user declined to run this tool. Do not retry it; tell the user it was not run."


class ToolHost(Protocol):
    """What the agent needs from MCPHub (tests can pass a stand-in)."""

    tools: dict[str, Any]

    def openai_tools(self) -> list[dict[str, Any]]: ...

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> str: ...


@dataclass
class ToolEvent:
    name: str
    arguments: dict[str, Any]
    result: str
    seconds: float
    approved: bool = True


@dataclass
class Agent:
    client: Any  # openai.OpenAI-compatible client (or demo.ScriptedLLM)
    model: str
    host: ToolHost
    max_steps: int = 8
    approve: Callable[[str, dict[str, Any]], bool] | None = None  # None = deny risky tools
    on_tool_call: Callable[[ToolEvent], None] | None = None
    messages: list[dict[str, Any]] = field(default_factory=lambda: [{"role": "system", "content": SYSTEM_PROMPT}])

    async def ask(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        tools = self.host.openai_tools()

        for _ in range(self.max_steps):
            kwargs: dict[str, Any] = {"model": self.model, "messages": self.messages}
            if tools:
                kwargs["tools"] = tools
            # The OpenAI SDK is synchronous; run it in a thread so MCP I/O keeps flowing.
            response = await anyio.to_thread.run_sync(lambda: self.client.chat.completions.create(**kwargs))
            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []

            entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                entry["tool_calls"] = [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]
            self.messages.append(entry)

            if not tool_calls:
                return msg.content or ""

            for tc in tool_calls:
                result = await self._run_tool(tc.function.name, tc.function.arguments)
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        return "I stopped after too many tool steps. Try a simpler question."

    async def _run_tool(self, name: str, raw_arguments: str | None) -> str:
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except ValueError:
            return f"ERROR: arguments for {name} were not a valid JSON object"

        remote = self.host.tools.get(name)
        approved = True
        if remote is not None and getattr(remote, "needs_approval", False):
            approved = bool(self.approve and self.approve(name, arguments))

        start = time.perf_counter()
        result = await self.host.call(name, arguments) if approved else DECLINED
        event = ToolEvent(name, arguments, result, time.perf_counter() - start, approved)
        if self.on_tool_call:
            self.on_tool_call(event)
        return result
