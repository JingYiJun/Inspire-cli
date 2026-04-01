from __future__ import annotations

import subprocess
from typing import Any

import pytest


def test_build_exec_command_wraps_cwd_and_env() -> None:
    from inspire.mcp.runtime import build_exec_command

    command = build_exec_command(
        "python -V",
        cwd="/tmp/work space",
        env={"FOO": "bar baz", "HELLO": "world"},
    )

    assert "cd '/tmp/work space'" in command
    assert "export FOO='bar baz'" in command
    assert "export HELLO=world" in command
    assert command.endswith("python -V")


def test_exec_remote_command_returns_normalized_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspire.mcp.runtime as runtime_module

    captured: dict[str, Any] = {}

    def fake_run_ssh_command(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runtime_module, "run_ssh_command", fake_run_ssh_command)

    result = runtime_module.exec_remote_command(
        bridge="cpu-main",
        command="pwd",
        cwd="/tmp/demo",
        env={"A": "1"},
        timeout_s=9,
    )

    assert result["ok"] is True
    assert result["bridge"] == "cpu-main"
    assert result["exit_code"] == 0
    assert result["stdout"] == "ok"
    assert result["stderr"] == ""
    assert captured["kwargs"]["bridge_name"] == "cpu-main"
    assert captured["kwargs"]["timeout"] == 9
    assert "cd /tmp/demo" in captured["kwargs"]["command"]
    assert "export A=1" in captured["kwargs"]["command"]


def test_exec_remote_command_maps_bridge_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    from inspire.bridge.tunnel.models import BridgeNotFoundError
    import inspire.mcp.runtime as runtime_module

    def fake_run_ssh_command(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        raise BridgeNotFoundError("Bridge 'missing' not found")

    monkeypatch.setattr(runtime_module, "run_ssh_command", fake_run_ssh_command)

    with pytest.raises(runtime_module.McpToolError) as exc_info:
        runtime_module.exec_remote_command(bridge="missing", command="pwd")

    assert exc_info.value.code == "bridge_not_found"
    assert "missing" in str(exc_info.value)
