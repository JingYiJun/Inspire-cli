"""MCP wrapper for bridge-backed rsync sync operations."""

from __future__ import annotations

from typing import Literal

from inspire.bridge.tunnel import sync_paths_via_rsync

from .errors import McpToolError


def sync_paths(
    *,
    bridge: str,
    source_path: str,
    target_path: str,
    direction: Literal["push", "pull"],
    delete: bool = False,
    timeout_s: int = 300,
    mkdir_parent: bool = True,
    exclude: list[str] | None = None,
) -> dict:
    """Sync a local/remote file or directory over the bridge rsync tunnel."""
    result = sync_paths_via_rsync(
        source_path=source_path,
        target_path=target_path,
        direction=direction,
        bridge_name=bridge,
        timeout=timeout_s,
        delete=delete,
        mkdir_parent=mkdir_parent,
        exclude=exclude,
    )
    if not result.get("success"):
        raise McpToolError(
            "sync_failed",
            str(result.get("error") or "Sync failed"),
            {
                "bridge": bridge,
                "direction": direction,
                "source_path": source_path,
                "target_path": target_path,
            },
        )
    return {
        "ok": True,
        "bridge": bridge,
        "direction": result.get("direction", direction),
        "source_path": source_path,
        "target_path": target_path,
        "source_kind": result.get("source_kind"),
        "delete": bool(result.get("delete", False)),
        "method": result.get("method", "rsync"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }
