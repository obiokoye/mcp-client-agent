"""ScriptedLLM: a fake, OpenAI-shaped client for demo mode and tests.

It reads the latest user message, picks a scripted plan (which MCP tools to
call and with what arguments), and once the real tool results come back it
writes an answer from them. The tool calls still go through MCP to the real
servers, so everything except the model itself is genuine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Any, Callable

ToolCall = tuple[str, dict[str, Any]]
Answer = Callable[[list[str]], str]

SUGGESTIONS = [
    "What tools do you have?",
    "What notes do I have about interviews?",
    "Add a note: call Ada about the invoice on Friday",
    "How many days until 2027-01-01, and what's 15% of 84,000?",
    "Delete note 2",
]


@dataclass
class Plan:
    calls: list[ToolCall] = field(default_factory=list)
    answer: Answer = lambda results: ""


def _load(result: str) -> Any:
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return result


def _err(result: str) -> str | None:
    return result.removeprefix("ERROR: ") if result.startswith("ERROR:") else None


def _note_lines(notes: list[dict[str, Any]]) -> str:
    return "\n".join(f"• #{n['id']} {n['title']}: {n['body']}" for n in notes)


def _tools_answer(tool_names: list[str]) -> str:
    by_server: dict[str, list[str]] = {}
    for name in tool_names:
        server, _, tool = name.partition("__")
        by_server.setdefault(server, []).append(tool)
    lines = [f"• {server}: {', '.join(tools)}" for server, tools in by_server.items()]
    return f"I'm connected to {len(by_server)} MCP servers with {len(tool_names)} tools:\n" + "\n".join(lines)


def plan_for(text: str, tool_names: list[str]) -> Plan:
    t = text.lower()

    if "tool" in t or "what can you do" in t:
        return Plan(answer=lambda _r: _tools_answer(tool_names))

    if m := re.search(r"(?:add|save) (?:a )?note:?\s*(.+)", text, re.IGNORECASE):
        body = m.group(1).strip().rstrip(".")
        title = body[:1].upper() + body[1:48]
        tags = "work,billing" if "invoice" in t else "todo"

        def saved(results: list[str]) -> str:
            if err := _err(results[0]):
                return f"I couldn't save that note: {err}"
            note = _load(results[0])
            return f"Saved note #{note['id']} “{note['title']}” with tags {note['tags']}."

        return Plan([("notes__add_note", {"title": title, "body": body, "tags": tags})], saved)

    if m := re.search(r"delete note #?(\d+)", t):

        def deleted(results: list[str]) -> str:
            if "declined" in results[0]:
                return f"OK, I didn't delete note #{m.group(1)}. Nothing was changed."
            if err := _err(results[0]):
                return f"I couldn't delete it: {err}"
            note = _load(results[0])["deleted"]
            return f"Deleted note #{note['id']} “{note['title']}”."

        return Plan([("notes__delete_note", {"note_id": int(m.group(1))})], deleted)

    if "note" in t:
        query = next((w for w in ("interview", "invoice", "mcp", "ada") if w in t), "")
        if query:

            def found(results: list[str]) -> str:
                notes = _load(results[0])
                if not notes:
                    return f"You have no notes about “{query}”."
                return f"You have {len(notes)} note(s) about “{query}”:\n{_note_lines(notes)}"

            return Plan([("notes__search_notes", {"query": query})], found)
        return Plan([("notes__list_notes", {"limit": 5})], lambda r: f"Your latest notes:\n{_note_lines(_load(r[0]))}")

    calls: list[ToolCall] = []
    if m := re.search(r"days until (\d{4}-\d{2}-\d{2})", t):
        calls.append(("utils__days_until", {"target_date": m.group(1)}))
    pct = re.search(r"([\d.]+)% of ([\d,]+)", t)
    if pct:
        calls.append(("utils__calculator", {"expression": f"{pct.group(2).replace(',', '')} * {pct.group(1)} / 100"}))
    if calls:

        def maths(results: list[str]) -> str:
            parts = []
            for (name, _args), raw in zip(calls, results):
                if err := _err(raw):
                    parts.append(f"{name} failed: {err}")
                    continue
                data = _load(raw)
                if name == "utils__days_until":
                    parts.append(f"There are {data['days']} days until {data['target']}.")
                else:
                    value = data["result"]
                    assert pct is not None
                    parts.append(f"{pct.group(1)}% of {pct.group(2)} is {value:,.0f}.")
            return " ".join(parts)

        return Plan(calls, maths)

    return Plan(answer=lambda _r: "Demo mode only knows a few scripted questions. Try:\n" + "\n".join(f"• {s}" for s in SUGGESTIONS))


class ScriptedLLM:
    """Duck-types `openai.OpenAI().chat.completions.create(...)`."""

    def __init__(self) -> None:
        self.chat = NS(completions=NS(create=self._create))
        self._counter = 0

    def _create(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **_: Any) -> NS:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        turn = messages[last_user + 1 :]
        tool_names = [t["function"]["name"] for t in tools or []]
        plan = plan_for(messages[last_user]["content"], tool_names)

        already_called = any(m["role"] == "assistant" for m in turn)
        if plan.calls and not already_called:
            missing = [name for name, _ in plan.calls if name not in tool_names]
            if missing:
                return self._reply(f"Demo mode: that needs {missing[0]}, but its MCP server isn't connected.")
            return self._reply(tool_calls=[self._tool_call(name, args) for name, args in plan.calls])

        results = [m["content"] for m in turn if m["role"] == "tool"]
        return self._reply(plan.answer(results))

    def _tool_call(self, name: str, args: dict[str, Any]) -> NS:
        self._counter += 1
        return NS(id=f"call_{self._counter}", function=NS(name=name, arguments=json.dumps(args)))

    @staticmethod
    def _reply(content: str | None = None, tool_calls: list[NS] | None = None) -> NS:
        return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])
