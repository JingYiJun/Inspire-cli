from pathlib import Path
from typing import Any

from inspire.bridge.tunnel.models import BridgeProfile, TunnelConfig
from inspire.bridge.tunnel import sync as sync_module


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_sync_via_rsync_runs_preflight_and_rsync(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "train.py").write_text("print('ok')\n", encoding="utf-8")

    tunnel_config = TunnelConfig()
    bridge = BridgeProfile(name="cpu-main", proxy_url="https://bridge.example.com")
    tunnel_config.add_bridge(bridge)

    captured: dict[str, Any] = {}

    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(
        sync_module,
        "_resolve_bridge_and_proxy",
        lambda bridge_name, config, quiet=True: (tunnel_config, bridge, "rtunnel client --stdio"),
    )

    def fake_run_ssh_command(command: str, *args: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["preflight"] = command
        captured["preflight_kwargs"] = kwargs
        return FakeCompletedProcess(returncode=0)

    def fake_subprocess_run(args: list[str], *unused: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["rsync_args"] = args
        captured["rsync_kwargs"] = kwargs
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(sync_module, "run_ssh_command", fake_run_ssh_command)
    monkeypatch.setattr(sync_module.subprocess, "run", fake_subprocess_run)

    result = sync_module.sync_via_rsync(
        source_dir=str(source_dir),
        target_dir="/remote/project",
        bridge_name="cpu-main",
        config=tunnel_config,
        timeout=123,
        delete=True,
    )

    assert result["success"] is True
    assert result["bridge_name"] == "cpu-main"
    assert "command -v rsync" in captured["preflight"]
    assert "mkdir -p /remote/project" in captured["preflight"]
    assert captured["preflight_kwargs"]["bridge_name"] == "cpu-main"

    rsync_args = captured["rsync_args"]
    assert rsync_args[0] == "rsync"
    assert "-az" in rsync_args
    assert "--numeric-ids" in rsync_args
    assert "--exclude=.inspire/" in rsync_args
    assert "--delete" in rsync_args
    assert any("ProxyCommand=rtunnel client --stdio" in arg for arg in rsync_args)
    assert any(arg.endswith("/src/") for arg in rsync_args)
    assert rsync_args[-1] == "root@localhost:/remote/project/"
    assert captured["rsync_kwargs"]["timeout"] == 123


def test_sync_via_rsync_can_skip_delete(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    tunnel_config = TunnelConfig()
    bridge = BridgeProfile(name="cpu-main", proxy_url="https://bridge.example.com")
    tunnel_config.add_bridge(bridge)

    captured: dict[str, Any] = {}

    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(
        sync_module,
        "_resolve_bridge_and_proxy",
        lambda bridge_name, config, quiet=True: (tunnel_config, bridge, "rtunnel client --stdio"),
    )
    monkeypatch.setattr(
        sync_module,
        "run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(returncode=0),
    )

    def fake_subprocess_run(args: list[str], *unused: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["rsync_args"] = args
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(sync_module.subprocess, "run", fake_subprocess_run)

    result = sync_module.sync_via_rsync(
        source_dir=str(source_dir),
        target_dir="/remote/project",
        bridge_name="cpu-main",
        config=tunnel_config,
        delete=False,
    )

    assert result["success"] is True
    assert "--delete" not in captured["rsync_args"]


def test_sync_via_rsync_returns_error_when_remote_preflight_fails(
    monkeypatch, tmp_path: Path
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    tunnel_config = TunnelConfig()
    bridge = BridgeProfile(name="cpu-main", proxy_url="https://bridge.example.com")
    tunnel_config.add_bridge(bridge)

    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(
        sync_module,
        "_resolve_bridge_and_proxy",
        lambda bridge_name, config, quiet=True: (tunnel_config, bridge, "rtunnel client --stdio"),
    )
    monkeypatch.setattr(
        sync_module,
        "run_ssh_command",
        lambda *args, **kwargs: FakeCompletedProcess(
            returncode=1, stderr="rsync missing on bridge"
        ),
    )

    result = sync_module.sync_via_rsync(
        source_dir=str(source_dir),
        target_dir="/remote/project",
        bridge_name="cpu-main",
        config=tunnel_config,
    )

    assert result["success"] is False
    assert "rsync missing on bridge" in result["error"]


def test_sync_paths_via_rsync_push_file_to_remote_file(monkeypatch, tmp_path: Path) -> None:
    source_file = tmp_path / "train.py"
    source_file.write_text("print('ok')\n", encoding="utf-8")

    tunnel_config = TunnelConfig()
    bridge = BridgeProfile(name="cpu-main", proxy_url="https://bridge.example.com")
    tunnel_config.add_bridge(bridge)

    captured: dict[str, Any] = {}

    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(
        sync_module,
        "_resolve_bridge_and_proxy",
        lambda bridge_name, config, quiet=True: (tunnel_config, bridge, "rtunnel client --stdio"),
    )

    def fake_run_ssh_command(command: str, *args: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["preflight"] = command
        return FakeCompletedProcess(returncode=0)

    def fake_subprocess_run(args: list[str], *unused: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["rsync_args"] = args
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(sync_module, "run_ssh_command", fake_run_ssh_command)
    monkeypatch.setattr(sync_module.subprocess, "run", fake_subprocess_run)

    result = sync_module.sync_paths_via_rsync(
        source_path=str(source_file),
        target_path="/remote/project/train.py",
        direction="push",
        bridge_name="cpu-main",
        config=tunnel_config,
    )

    assert result["success"] is True
    assert result["source_kind"] == "file"
    assert "mkdir -p /remote/project" in captured["preflight"]
    assert captured["rsync_args"][-2] == str(source_file.resolve())
    assert captured["rsync_args"][-1] == "root@localhost:/remote/project/train.py"
    assert "--delete" not in captured["rsync_args"]


def test_sync_paths_via_rsync_pull_directory_to_local_directory(
    monkeypatch, tmp_path: Path
) -> None:
    target_dir = tmp_path / "downloaded"

    tunnel_config = TunnelConfig()
    bridge = BridgeProfile(name="cpu-main", proxy_url="https://bridge.example.com")
    tunnel_config.add_bridge(bridge)

    captured: dict[str, Any] = {}

    monkeypatch.setattr(sync_module.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(
        sync_module,
        "_resolve_bridge_and_proxy",
        lambda bridge_name, config, quiet=True: (tunnel_config, bridge, "rtunnel client --stdio"),
    )

    def fake_run_ssh_command(command: str, *args: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["preflight"] = command
        return FakeCompletedProcess(returncode=0, stdout="directory\n")

    def fake_subprocess_run(args: list[str], *unused: Any, **kwargs: Any) -> FakeCompletedProcess:
        captured["rsync_args"] = args
        return FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(sync_module, "run_ssh_command", fake_run_ssh_command)
    monkeypatch.setattr(sync_module.subprocess, "run", fake_subprocess_run)

    result = sync_module.sync_paths_via_rsync(
        source_path="/remote/project",
        target_path=str(target_dir),
        direction="pull",
        bridge_name="cpu-main",
        config=tunnel_config,
        delete=True,
    )

    assert result["success"] is True
    assert result["source_kind"] == "directory"
    assert target_dir.exists()
    assert "test -d /remote/project" in captured["preflight"]
    assert captured["rsync_args"][-2] == "root@localhost:/remote/project/"
    assert captured["rsync_args"][-1] == str(target_dir.resolve()) + "/"
    assert "--delete" in captured["rsync_args"]
