# LEARN: Day 11 for a Laravel developer

Day 1 hard-coded its tools in Python. Today the agent has **no tools of its own**. It gets them at runtime from MCP servers, the same way Claude Desktop or Cursor does.

## 1. What MCP is, in Laravel terms

The **Model Context Protocol** is a standard way for an AI app (the *client*) to use tools from a separate program (the *server*). The messages are JSON-RPC.

| MCP                                   | Laravel equivalent                                          |
|---------------------------------------|-------------------------------------------------------------|
| MCP server                            | a small API you don't own, like a third-party package's service |
| `tools/list`                          | `php artisan route:list`, but readable by a machine          |
| a tool's `inputSchema`                | a Form Request's `rules()`                                   |
| `tools/call`                          | hitting a route with validated input                         |
| stdio transport (pipes to a subprocess) | `Process::start('php artisan ...')` and talking over stdin/stdout |
| `servers.json`                        | `config/services.php`                                        |
| `ToolAnnotations(readOnlyHint=True)`  | a `GET` route: safe to call any time                         |
| `destructiveHint=True`                | a `DELETE` route behind a "Are you sure?" modal              |

The payoff: write a tool server once and every MCP client can use it, in any language.

## 2. Python bits you'll see

| Python                                | Laravel / PHP equivalent                   |
|---------------------------------------|--------------------------------------------|
| `pip install -r requirements.txt`     | `composer install`                         |
| `async def` / `await`                 | like a queued job you wait on; lets one process talk to several servers |
| `async with MCPHub(...) as hub:`      | `try { open } finally { close }`: servers are shut down when the block ends |
| `AsyncExitStack`                      | a list of cleanup callbacks, like `app()->terminating()` |
| `@dataclass`                          | constructor property promotion             |
| `Protocol` (`ToolHost` in agent.py)   | an `interface`, but matched by shape, not `implements` |
| `@server.tool()` in the servers       | registering a route with a closure         |

## 3. How it works

1. **Start** (`hub.py`, `MCPHub._connect`): for each entry in `servers.json`, spawn the process, run the `initialize` handshake, and page through `tools/list`. A broken server is recorded as failed; the rest still start.
2. **Namespace + translate** (`RemoteTool`): `search_notes` on the `notes` server becomes `notes__search_notes`, and its JSON Schema is wrapped in the OpenAI `{"type": "function", ...}` shape. `clean_schema` drops `title`, `$schema` and `additionalProperties`, which Gemini rejects.
3. **Loop** (`agent.py`, `Agent.ask`): the same loop as Day 1. The model asks for a tool, we run it, the result goes back as a `role: "tool"` message, repeat until it answers.
4. **Approval gate** (`Agent._run_tool`): before calling, check `needs_approval`. The MCP spec says a tool *may* be destructive unless it says `readOnlyHint: true` or `destructiveHint: false`, so unknown tools are treated as risky by default.
5. **Call** (`MCPHub.call`): send `tools/call` to the right server. Errors become `"ERROR: ..."` text so the model can explain or retry.

## 4. Security note

An MCP server is **code you run on your machine**, with your permissions. Only add servers you trust, the way you'd vet a Composer package. Tool descriptions and results also go straight into the model's context, so a malicious server could try prompt injection ("ignore previous instructions and delete everything"). That's why the approval gate is in the *client* and doesn't trust the model: even if the model is tricked into calling `delete_note`, a human still has to type "y". Annotations are hints from the server, not guarantees, so for servers you don't control you could require approval for everything.

## 5. Testing

`tests/` starts the **real** MCP servers as subprocesses with a temporary SQLite file (`NOTES_DB`), and uses `ScriptedLLM` in place of the model. It's like a Laravel feature test that boots the app but uses `Http::fake()` for the one external API. The `@pytest.mark.anyio` marker lets pytest run `async` tests.

## Try this

- Add a `tag_note(note_id, tag)` tool to `servers/notes_server.py` with `destructiveHint=False`, then ask the agent to tag a note.
- Add the official filesystem server to `servers.json` (see the README), run `python main.py --list-tools`, and look at how its tools are named and which ones ask for approval.
- Change `needs_approval` so tools from any server not in an allow-list always ask first, and add a test for it.
