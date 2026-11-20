"""Config parsing, schema conversion and real MCP connections (no API key needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations

from mcp_agent.config import ConfigError, ServerConfig, load_config, parse_config
from mcp_agent.hub import MAX_RESULT_CHARS, MCPHub, RemoteTool, clean_schema, format_result, qualify

# --- config -----------------------------------------------------------------


def test_parse_config_expands_python_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTES_HOME", "/srv/notes")
    data = {"mcpServers": {"notes": {"command": "${PYTHON}", "args": ["s.py"], "env": {"DB": "${NOTES_HOME}/n.db"}}}}
    [cfg] = parse_config(data, tmp_path)
    assert cfg.command == sys.executable
    assert cfg.env == {"DB": "/srv/notes/n.db"}
    assert cfg.cwd == str(tmp_path)


def test_parse_config_skips_disabled_and_validates(tmp_path):
    ok = {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y", "disabled": True}}}
    assert [c.name for c in parse_config(ok, tmp_path)] == ["a"]
    with pytest.raises(ConfigError, match="command"):
        parse_config({"mcpServers": {"a": {"url": "https://example.com/mcp"}}}, tmp_path)
    with pytest.raises(ConfigError, match="server name"):
        parse_config({"mcpServers": {"bad name!": {"command": "x"}}}, tmp_path)
    with pytest.raises(ConfigError, match="non-empty"):
        parse_config({}, tmp_path)


def test_load_config_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    with pytest.raises(ConfigError, match="valid JSON"):
        load_config(bad)


def test_bundled_servers_json_is_valid():
    names = [c.name for c in load_config(Path(__file__).resolve().parent.parent / "servers.json")]
    assert names == ["notes", "utils"]


# --- tool conversion --------------------------------------------------------


def test_qualify_namespaces_and_sanitises():
    assert qualify("notes", "search_notes") == "notes__search_notes"
    assert qualify("web", "fetch page!") == "web__fetch_page_"
    assert len(qualify("s", "x" * 100)) == 64


def test_clean_schema_keeps_a_property_called_title():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "addArguments",
        "type": "object",
        "additionalProperties": False,
        "properties": {"title": {"title": "Title", "type": "string"}},
    }
    assert clean_schema(schema) == {"type": "object", "properties": {"title": {"type": "string"}}}


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        (None, True),  # spec default: unknown tools may be destructive
        (ToolAnnotations(readOnlyHint=True), False),
        (ToolAnnotations(readOnlyHint=False, destructiveHint=False), False),
        (ToolAnnotations(readOnlyHint=False, destructiveHint=True), True),
        (ToolAnnotations(readOnlyHint=False), True),
    ],
)
def test_needs_approval_follows_mcp_hints(annotations, expected):
    tool = Tool(name="t", input_schema={"type": "object"}, annotations=annotations)
    assert RemoteTool("s", tool).needs_approval is expected


def test_to_openai_shape():
    tool = Tool(name="search", description="Find things", input_schema={"type": "object", "properties": {"q": {"type": "string"}}})
    fn = RemoteTool("docs", tool).to_openai()
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "docs__search"
    assert fn["function"]["description"] == "[docs] Find things"
    assert fn["function"]["parameters"]["properties"] == {"q": {"type": "string"}}


def test_format_result_variants():
    structured = CallToolResult(content=[TextContent(type="text", text="5")], structured_content={"result": 5})
    assert format_result(structured) == "5"
    error = CallToolResult(content=[TextContent(type="text", text="boom")], is_error=True)
    assert format_result(error) == "ERROR: boom"
    image = CallToolResult(content=[ImageContent(type="image", data="aGk=", mime_type="image/png")])
    assert format_result(image) == "[image content omitted]"
    huge = CallToolResult(content=[TextContent(type="text", text="x" * (MAX_RESULT_CHARS + 50))])
    assert format_result(huge).endswith("[truncated 50 chars]")


# --- real MCP servers over stdio -------------------------------------------


@pytest.mark.anyio
async def test_hub_discovers_and_calls_tools(server_configs):
    async with MCPHub(server_configs) as hub:
        assert all(s.ok for s in hub.status)
        assert set(hub.tools) == {
            "notes__list_notes", "notes__search_notes", "notes__add_note", "notes__delete_note",
            "utils__calculator", "utils__days_until", "utils__current_time",
        }
        assert hub.tools["notes__delete_note"].needs_approval
        assert not hub.tools["notes__add_note"].needs_approval

        found = json.loads(await hub.call("notes__search_notes", {"query": "interview"}))
        assert [n["id"] for n in found] == [1, 3]

        maths = json.loads(await hub.call("utils__calculator", {"expression": "84000 * 0.15"}))
        assert maths["result"] == pytest.approx(12600)

        assert (await hub.call("utils__calculator", {"expression": "__import__('os')"})).startswith("ERROR")
        assert "no note with id 99" in await hub.call("notes__delete_note", {"note_id": 99})
        assert await hub.call("nope__tool", {}) == "ERROR: unknown tool 'nope__tool'"


@pytest.mark.anyio
async def test_one_broken_server_does_not_stop_the_others(server_configs, tmp_path):
    broken = ServerConfig("broken", sys.executable, ["-c", "import sys; sys.exit(1)"])
    with open(tmp_path / "err.log", "w") as errlog:
        async with MCPHub([broken, server_configs[1]], errlog=errlog, timeout_seconds=10) as hub:
            status = {s.name: s for s in hub.status}
            assert not status["broken"].ok and status["broken"].error
            assert status["utils"].ok and status["utils"].tools == 3
