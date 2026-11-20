"""MCPHub: connect to several MCP servers and expose their tools as one list.

What happens on start-up:
1. Spawn each server as a subprocess and talk JSON-RPC over its stdin/stdout.
2. `initialize` (handshake), then `tools/list` to discover what it offers.
3. Give every tool a namespaced name, `<server>__<tool>`, so two servers can
   both have a `search` tool without clashing.
4. Convert each MCP tool's JSON Schema into the OpenAI "function" format
   that every LLM provider in providers.py understands.

The agent never imports a server's code. Anything that speaks MCP works.
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, PaginatedRequestParams, Tool

from .config import ServerConfig

SEP = "__"
MAX_RESULT_CHARS = 4000
# JSON Schema keys some providers (notably Gemini) reject in tool parameters.
_UNSUPPORTED_SCHEMA_KEYS = {"$schema", "title", "additionalProperties"}


@dataclass
class RemoteTool:
    server: str
    tool: Tool

    @property
    def qualified_name(self) -> str:
        return qualify(self.server, self.tool.name)

    @property
    def needs_approval(self) -> bool:
        """MCP spec: a tool may be destructive unless it says it's read-only or destructiveHint=false."""
        hints = self.tool.annotations
        if hints is None:
            return True
        if hints.read_only_hint:
            return False
        return hints.destructive_hint is not False

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.qualified_name,
                "description": f"[{self.server}] {self.tool.description or self.tool.name}"[:1024],
                "parameters": clean_schema(self.tool.input_schema or {"type": "object", "properties": {}}),
            },
        }


@dataclass
class ServerStatus:
    name: str
    ok: bool
    tools: int = 0
    error: str = ""


def qualify(server: str, tool: str) -> str:
    """'notes' + 'search notes!' -> 'notes__search_notes_' (OpenAI names: [a-zA-Z0-9_-], max 64)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}{SEP}{tool}")[:64]


def clean_schema(schema: Any) -> Any:
    """Recursively drop schema keys that strict providers refuse."""
    if isinstance(schema, dict):
        cleaned = {k: clean_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        # Keep property names even if a property is literally called "title".
        if isinstance(schema.get("properties"), dict):
            cleaned["properties"] = {k: clean_schema(v) for k, v in schema["properties"].items()}
        return cleaned
    if isinstance(schema, list):
        return [clean_schema(s) for s in schema]
    return schema


def format_result(result: CallToolResult) -> str:
    """Turn an MCP tool result into the text we send back to the model."""
    if result.structured_content is not None:
        data = result.structured_content
        # MCPServer wraps non-object return values as {"result": ...}.
        if isinstance(data, dict) and list(data) == ["result"]:
            data = data["result"]
        text = json.dumps(data, ensure_ascii=False)
    else:
        parts = []
        for block in result.content:
            if block.type == "text":
                parts.append(block.text)
            else:
                parts.append(f"[{block.type} content omitted]")
        text = "\n".join(parts)
    if result.is_error:
        text = f"ERROR: {text}"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"... [truncated {len(text) - MAX_RESULT_CHARS} chars]"
    return text


@dataclass
class MCPHub:
    servers: list[ServerConfig]
    errlog: TextIO = field(default_factory=lambda: sys.stderr)
    timeout_seconds: float = 30.0
    tools: dict[str, RemoteTool] = field(default_factory=dict)
    status: list[ServerStatus] = field(default_factory=list)
    _sessions: dict[str, ClientSession] = field(default_factory=dict)
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack)

    async def __aenter__(self) -> MCPHub:
        await self._stack.__aenter__()
        for cfg in self.servers:
            await self._connect(cfg)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.__aexit__(*exc)

    async def _connect(self, cfg: ServerConfig) -> None:
        params = StdioServerParameters(command=cfg.command, args=cfg.args, env=cfg.env, cwd=cfg.cwd)
        # Each server gets its own stack so one broken server can't take the others down.
        server_stack = AsyncExitStack()
        try:
            read, write = await server_stack.enter_async_context(stdio_client(params, errlog=self.errlog))
            session = await server_stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=self.timeout_seconds)
            )
            await session.initialize()
            found = await self._list_all_tools(session)
        except Exception as exc:  # noqa: BLE001 - report any start-up failure and carry on
            await server_stack.aclose()
            self.status.append(ServerStatus(cfg.name, ok=False, error=f"{type(exc).__name__}: {exc}"[:200]))
            return

        self._stack.push_async_callback(server_stack.aclose)
        self._sessions[cfg.name] = session
        for tool in found:
            remote = RemoteTool(cfg.name, tool)
            self.tools[remote.qualified_name] = remote
        self.status.append(ServerStatus(cfg.name, ok=True, tools=len(found)))

    @staticmethod
    async def _list_all_tools(session: ClientSession) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return tools

    def openai_tools(self) -> list[dict[str, Any]]:
        return [t.to_openai() for t in self.tools.values()]

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        remote = self.tools.get(qualified_name)
        if remote is None:
            return f"ERROR: unknown tool '{qualified_name}'"
        session = self._sessions[remote.server]
        try:
            result = await session.call_tool(remote.tool.name, arguments, read_timeout_seconds=self.timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - the model should see the failure, not crash the chat
            return f"ERROR: {type(exc).__name__}: {exc}"
        return format_result(result)
