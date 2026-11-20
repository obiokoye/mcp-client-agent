"""Load MCP server definitions from a JSON file.

The format is the same "mcpServers" block that Claude Desktop, Cursor and
VS Code use, so you can paste server entries straight from their READMEs:

    {"mcpServers": {"notes": {"command": "python", "args": ["servers/notes_server.py"]}}}

`${VAR}` placeholders are expanded from the environment, and `${PYTHON}` is
the Python running this program (handy on Windows, where it's inside .venv).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


def expand(value: str, extra: dict[str, str] | None = None) -> str:
    """Replace ${VAR} with environment values (unknown variables become empty strings)."""
    lookup = {"PYTHON": sys.executable, **os.environ, **(extra or {})}
    return _VAR.sub(lambda m: lookup.get(m.group(1), ""), value)


def parse_config(data: dict[str, Any], base_dir: Path) -> list[ServerConfig]:
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        raise ConfigError("config needs a non-empty 'mcpServers' object")

    result: list[ServerConfig] = []
    for name, spec in servers.items():
        if not _NAME.match(name):
            raise ConfigError(f"server name '{name}' must be 1-32 letters, digits, '-' or '_'")
        if not isinstance(spec, dict) or not isinstance(spec.get("command"), str):
            raise ConfigError(f"server '{name}' needs a 'command' string (only stdio servers are supported)")
        if spec.get("disabled"):
            continue
        args = spec.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ConfigError(f"server '{name}': 'args' must be a list of strings")
        env = spec.get("env")
        if env is not None and not isinstance(env, dict):
            raise ConfigError(f"server '{name}': 'env' must be an object")
        result.append(
            ServerConfig(
                name=name,
                command=expand(spec["command"]),
                args=[expand(a) for a in args],
                env={k: expand(str(v)) for k, v in env.items()} if env else None,
                cwd=expand(spec["cwd"]) if spec.get("cwd") else str(base_dir),
            )
        )
    return result


def load_config(path: str | Path) -> list[ServerConfig]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    return parse_config(data, path.resolve().parent)
