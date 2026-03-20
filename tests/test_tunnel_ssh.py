import subprocess
from pathlib import Path
from typing import Any

from inspire.bridge.tunnel.models import BridgeProfile, TunnelConfig
import inspire.bridge.tunnel.ssh as ssh_module


def test_test_ssh_connection_uses_devnull_stdin(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = TunnelConfig()

    fake_rtunnel = tmp_path / "rtunnel"
    fake_rtunnel.write_text("#!/bin/sh\nexit 0\n")
    fake_rtunnel.chmod(0o755)

    bridge = BridgeProfile(name="gpu-main", proxy_url="https://proxy.example.com/proxy/31337/")
    captured: dict[str, Any] = {}

    monkeypatch.setattr(ssh_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    assert ssh_module._test_ssh_connection(bridge=bridge, config=config) is True
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL
