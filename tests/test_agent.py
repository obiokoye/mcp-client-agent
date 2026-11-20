"""The agent loop with the scripted LLM and real MCP servers. No API key or network."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

import pytest

from mcp_agent import Agent, MCPHub, ToolEvent
from mcp_agent.agent import DECLINED
from mcp_agent.demo import ScriptedLLM


@pytest.mark.anyio
async def test_agent_answers_from_two_tools(server_configs):
    events: list[ToolEvent] = []
    async with MCPHub(server_configs) as hub:
        agent = Agent(ScriptedLLM(), "demo", hub, on_tool_call=events.append)
        answer = await agent.ask("How many days until 2027-01-01, and what's 15% of 84,000?")

    assert [e.name for e in events] == ["utils__days_until", "utils__calculator"]
    assert "15% of 84,000 is 12,600." in answer
    # Both tool results were sent back to the model as role=tool messages.
    assert [m["role"] for m in agent.messages[-4:]] == ["assistant", "tool", "tool", "assistant"]


@pytest.mark.anyio
async def test_destructive_tool_is_declined_without_approval(server_configs):
    events: list[ToolEvent] = []
    async with MCPHub(server_configs) as hub:
        agent = Agent(ScriptedLLM(), "demo", hub, on_tool_call=events.append)  # approve=None
        answer = await agent.ask("Delete note 2")
        still_there = json.loads(await hub.call("notes__search_notes", {"query": "invoice"}))

    assert events[0].approved is False and events[0].result == DECLINED
    assert "didn't delete" in answer
    assert [n["id"] for n in still_there] == [2]


@pytest.mark.anyio
async def test_destructive_tool_runs_when_approved(server_configs):
    asked: list[tuple[str, dict[str, Any]]] = []

    def approve(name: str, args: dict[str, Any]) -> bool:
        asked.append((name, args))
        return True

    async with MCPHub(server_configs) as hub:
        agent = Agent(ScriptedLLM(), "demo", hub, approve=approve)
        answer = await agent.ask("Delete note 2")
        remaining = json.loads(await hub.call("notes__list_notes", {}))

    assert asked == [("notes__delete_note", {"note_id": 2})]
    assert answer == "Deleted note #2 “Invoice for Ada”."
    assert 2 not in [n["id"] for n in remaining]


@pytest.mark.anyio
async def test_add_then_search_round_trip(server_configs):
    async with MCPHub(server_configs) as hub:
        agent = Agent(ScriptedLLM(), "demo", hub)
        saved = await agent.ask("Add a note: call Ada about the invoice on Friday")
        found = await agent.ask("Any notes about Ada?")

    assert saved.startswith("Saved note #4")
    assert "#4 Call Ada about the invoice on Friday" in found


@pytest.mark.anyio
async def test_scripted_llm_reports_missing_server(server_configs):
    async with MCPHub(server_configs[1:]) as hub:  # only utils
        answer = await Agent(ScriptedLLM(), "demo", hub).ask("What notes do I have about interviews?")
    assert "isn't connected" in answer


# --- loop edge cases with a stand-in host -----------------------------------


class FakeHost:
    tools: dict[str, Any] = {"x__ping": NS(needs_approval=False)}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def openai_tools(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": "x__ping", "parameters": {"type": "object"}}}]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return "pong"


class LoopingLLM:
    def __init__(self, arguments: str = "{}") -> None:
        self.arguments = arguments
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **_: Any) -> NS:
        call = NS(id="c1", function=NS(name="x__ping", arguments=self.arguments))
        return NS(choices=[NS(message=NS(content=None, tool_calls=[call]))])


@pytest.mark.anyio
async def test_agent_stops_after_max_steps():
    host = FakeHost()
    answer = await Agent(LoopingLLM(), "fake", host, max_steps=3).ask("loop")
    assert "too many tool steps" in answer
    assert len(host.calls) == 3


@pytest.mark.anyio
async def test_invalid_json_arguments_are_reported_not_run():
    host = FakeHost()
    agent = Agent(LoopingLLM("{not json"), "fake", host, max_steps=1)
    await agent.ask("go")
    assert host.calls == []
    assert agent.messages[-1]["content"].startswith("ERROR: arguments for x__ping")
