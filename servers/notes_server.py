"""A small MCP server that stores notes in SQLite.

It is bundled so the agent has something real to connect to, but the agent
knows nothing about it: it discovers these tools at runtime over MCP.

Run it on its own with:  python servers/notes_server.py
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "notes.db"

SEED_NOTES = [
    ("Interview prep: MCP", "Explain MCP as 'USB-C for AI tools': one protocol, any client, any server.", "interview,ai"),
    ("Invoice for Ada", "Send the November invoice for the Laravel dashboard work by 28 Nov.", "work,billing"),
    ("Interview prep: agent loop", "Model asks for a tool, we run it, send the result back, repeat until it answers.", "interview,ai"),
]

READ_ONLY = ToolAnnotations(readOnlyHint=True)
ADDITIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

server = MCPServer("notes", instructions="Personal notes stored in SQLite.", log_level="WARNING")


def _connect() -> sqlite3.Connection:
    path = Path(os.getenv("NOTES_DB", str(DEFAULT_DB)))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, "
        "tags TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)"
    )
    if conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0 and os.getenv("NOTES_SEED", "1") == "1":
        today = date.today().isoformat()
        conn.executemany(
            "INSERT INTO notes (title, body, tags, created) VALUES (?, ?, ?, ?)",
            [(t, b, tags, today) for t, b, tags in SEED_NOTES],
        )
        conn.commit()
    return conn


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "title": row["title"], "body": row["body"], "tags": row["tags"], "created": row["created"]}


@server.tool(annotations=READ_ONLY)
def list_notes(limit: int = 20) -> list[dict[str, Any]]:
    """List the most recent notes (newest first)."""
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
    return [_row(r) for r in rows]


@server.tool(annotations=READ_ONLY)
def search_notes(query: str) -> list[dict[str, Any]]:
    """Find notes whose title, body or tags contain the query (case-insensitive)."""
    like = f"%{query.strip()}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? OR tags LIKE ? ORDER BY id",
            (like, like, like),
        ).fetchall()
    return [_row(r) for r in rows]


@server.tool(annotations=ADDITIVE)
def add_note(title: str, body: str, tags: str = "") -> dict[str, Any]:
    """Save a new note. Tags are comma-separated, e.g. 'work,billing'."""
    if not title.strip():
        raise ToolError("title must not be empty")
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO notes (title, body, tags, created) VALUES (?, ?, ?, ?)",
            (title.strip(), body.strip(), tags.strip(), date.today().isoformat()),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row(row)


@server.tool(annotations=DESTRUCTIVE)
def delete_note(note_id: int) -> dict[str, Any]:
    """Permanently delete a note by id."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise ToolError(f"no note with id {note_id}")
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return {"deleted": _row(row)}


if __name__ == "__main__":
    server.run()  # stdio transport
