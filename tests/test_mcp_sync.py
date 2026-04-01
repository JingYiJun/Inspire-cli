from __future__ import annotations

from typing import Any

import pytest


def test_sync_paths_returns_domain_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspire.mcp.sync as mcp_sync_module

    captured: dict[str, Any] = {}

    def fake_sync_paths_via_rsync(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "bridge_name": kwargs["bridge_name"],
            "direction": kwargs["direction"],
            "source_path": kwargs["source_path"],
            "target_path": kwargs["target_path"],
            "source_kind": "directory",
            "delete": kwargs["delete"],
            "method": "rsync",
        }

    monkeypatch.setattr(mcp_sync_module, "sync_paths_via_rsync", fake_sync_paths_via_rsync)

    result = mcp_sync_module.sync_paths(
        bridge="cpu-main",
        source_path="./src",
        target_path="/remote/project",
        direction="push",
    )

    assert result["ok"] is True
    assert result["bridge"] == "cpu-main"
    assert result["direction"] == "push"
    assert captured["bridge_name"] == "cpu-main"
    assert captured["delete"] is False


def test_sync_paths_maps_domain_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspire.mcp.sync as mcp_sync_module

    monkeypatch.setattr(
        mcp_sync_module,
        "sync_paths_via_rsync",
        lambda **kwargs: {"success": False, "error": "bad sync", "bridge_name": "cpu-main"},
    )

    with pytest.raises(mcp_sync_module.McpToolError) as exc_info:
        mcp_sync_module.sync_paths(
            bridge="cpu-main",
            source_path="./src",
            target_path="/remote/project",
            direction="push",
        )

    assert exc_info.value.code == "sync_failed"
    assert "bad sync" in str(exc_info.value)
