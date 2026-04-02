"""MCP server exposing Inspire bridge tools."""

from __future__ import annotations

import threading

import anyio
from mcp.server.fastmcp import FastMCP

from .errors import McpToolError
from .remote_fs import edit_file, list_dir, read_file, stat, write_file
from .runtime import exec_remote_command
from .sync import sync_paths

TOOL_NAMES = ("exec", "read_file", "write_file", "edit_file", "sync", "list_dir", "stat")


def _run_in_fresh_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
    result = {}
    error = {}

    def _runner() -> None:
        try:
            result["value"] = func(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


if not getattr(anyio, "_inspire_mcp_run_patched", False):
    _original_anyio_run = anyio.run

    def _anyio_run_compat(func, *args, backend="asyncio", backend_options=None):  # type: ignore[no-untyped-def]
        try:
            return _original_anyio_run(
                func,
                *args,
                backend=backend,
                backend_options=backend_options,
            )
        except RuntimeError as exc:
            if "Already running" not in str(exc):
                raise
            return _run_in_fresh_thread(
                _original_anyio_run,
                func,
                *args,
                backend=backend,
                backend_options=backend_options,
            )

    anyio.run = _anyio_run_compat
    anyio._inspire_mcp_run_patched = True  # type: ignore[attr-defined]


def build_server() -> FastMCP:
    server = FastMCP("inspire-bridge")

    @server.tool(name="exec")
    def exec_tool(
        bridge: str,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int | None = None,
        stream: bool = False,
    ) -> dict:
        del stream
        return exec_remote_command(
            bridge=bridge,
            command=command,
            cwd=cwd,
            env=env,
            timeout_s=timeout_s,
        )

    @server.tool(name="read_file")
    def read_file_tool(
        bridge: str,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        encoding: str = "utf-8",
    ) -> dict:
        return read_file(bridge=bridge, path=path, offset=offset, limit=limit, encoding=encoding)

    @server.tool(name="write_file")
    def write_file_tool(
        bridge: str,
        path: str,
        content: str,
        encoding: str = "utf-8",
        atomic: bool = True,
        mkdir_parent: bool = False,
        mode: int | None = None,
    ) -> dict:
        return write_file(
            bridge=bridge,
            path=path,
            content=content,
            encoding=encoding,
            atomic=atomic,
            mkdir_parent=mkdir_parent,
            mode=mode,
        )

    @server.tool(name="edit_file")
    def edit_file_tool(
        bridge: str,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        expected_sha256: str | None = None,
    ) -> dict:
        return edit_file(
            bridge=bridge,
            path=path,
            old_text=old_text,
            new_text=new_text,
            replace_all=replace_all,
            expected_sha256=expected_sha256,
        )

    @server.tool(name="sync")
    def sync_tool(
        bridge: str,
        source_path: str,
        target_path: str,
        direction: str,
        delete: bool = False,
        timeout_s: int = 300,
        mkdir_parent: bool = True,
        exclude: list[str] | None = None,
    ) -> dict:
        return sync_paths(
            bridge=bridge,
            source_path=source_path,
            target_path=target_path,
            direction=direction,  # type: ignore[arg-type]
            delete=delete,
            timeout_s=timeout_s,
            mkdir_parent=mkdir_parent,
            exclude=exclude,
        )

    @server.tool(name="list_dir")
    def list_dir_tool(
        bridge: str,
        path: str,
        recursive: bool = False,
        limit: int = 200,
        include_hidden: bool = False,
    ) -> dict:
        return list_dir(
            bridge=bridge,
            path=path,
            recursive=recursive,
            limit=limit,
            include_hidden=include_hidden,
        )

    @server.tool(name="stat")
    def stat_tool(
        bridge: str,
        path: str,
        follow_symlinks: bool = False,
    ) -> dict:
        return stat(bridge=bridge, path=path, follow_symlinks=follow_symlinks)

    return server


def main() -> None:
    server = build_server()
    server.run()


__all__ = ["TOOL_NAMES", "build_server", "main", "McpToolError"]
