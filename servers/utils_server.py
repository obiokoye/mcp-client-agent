"""A second MCP server with small read-only utilities (math and dates).

Run it on its own with:  python servers/utils_server.py
"""

from __future__ import annotations

import ast
import operator
from datetime import date, datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(readOnlyHint=True)

server = MCPServer("utils", instructions="Safe arithmetic and date helpers.", log_level="WARNING")

_OPS: dict[type, Callable[..., float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ToolError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ToolError("only numbers and + - * / // % ** are allowed")


@server.tool(annotations=READ_ONLY)
def calculator(expression: str) -> dict[str, Any]:
    """Evaluate an arithmetic expression like '84000 * 0.15'. No code, just maths."""
    try:
        return {"expression": expression, "result": _eval_node(ast.parse(expression, mode="eval").body)}
    except (SyntaxError, ValueError, ZeroDivisionError) as exc:
        raise ToolError(f"cannot evaluate '{expression}': {exc}") from exc


@server.tool(annotations=READ_ONLY)
def days_until(target_date: str, timezone: str = "Africa/Lagos") -> dict[str, Any]:
    """Whole days from today until a date in YYYY-MM-DD format (negative if it has passed)."""
    try:
        target = date.fromisoformat(target_date)
        today = datetime.now(ZoneInfo(timezone)).date()
    except ValueError as exc:
        raise ToolError("target_date must look like 2027-01-01") from exc
    except ZoneInfoNotFoundError as exc:
        raise ToolError(f"unknown timezone '{timezone}'") from exc
    return {"today": today.isoformat(), "target": target.isoformat(), "days": (target - today).days}


@server.tool(annotations=READ_ONLY)
def current_time(timezone: str = "Africa/Lagos") -> dict[str, Any]:
    """Current date and time in an IANA timezone such as 'Europe/London'."""
    try:
        now = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ToolError(f"unknown timezone '{timezone}'") from exc
    return {"timezone": timezone, "iso": now.isoformat(timespec="seconds"), "weekday": now.strftime("%A")}


if __name__ == "__main__":
    server.run()
