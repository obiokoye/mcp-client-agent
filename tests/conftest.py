from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mcp_agent.config import ServerConfig

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def server_configs(tmp_path: Path) -> list[ServerConfig]:
    """The two bundled servers, with the notes DB in a temp folder."""
    return [
        ServerConfig("notes", sys.executable, [str(ROOT / "servers" / "notes_server.py")], {"NOTES_DB": str(tmp_path / "n.db")}),
        ServerConfig("utils", sys.executable, [str(ROOT / "servers" / "utils_server.py")]),
    ]
