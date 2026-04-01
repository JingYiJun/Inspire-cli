from __future__ import annotations

import anyio
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_inspire_mcp_entrypoint_registered() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert data["project"]["dependencies"]
    assert any(dep.startswith("mcp") for dep in data["project"]["dependencies"])
    assert data["project"]["scripts"]["inspire-mcp"] == "inspire.mcp.server:main"


def test_build_server_registers_expected_tools() -> None:
    from inspire.mcp.server import TOOL_NAMES, build_server

    server = build_server()

    assert server is not None
    assert TOOL_NAMES == (
        "exec",
        "read_file",
        "write_file",
        "edit_file",
        "sync",
        "list_dir",
        "stat",
    )


def test_build_server_lists_expected_tool_names() -> None:
    from inspire.mcp.server import TOOL_NAMES, build_server

    async def _list_names() -> list[str]:
        server = build_server()
        tools = await server.list_tools()
        return [tool.name for tool in tools]

    assert anyio.run(_list_names) == list(TOOL_NAMES)
