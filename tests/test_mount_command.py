"""Tests for the experimental mount command."""

from __future__ import annotations

import importlib
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

mount_module = importlib.import_module("inspire.cli.commands.mount")
mount_start = mount_module.mount_start
mount_stop = mount_module.mount_stop
mount_status = mount_module.mount_status


def _make_config(*, target_dir: str | None = None, username: str | None = None):
    return SimpleNamespace(target_dir=target_dir, username=username)


def test_pick_local_port_honors_requested_port() -> None:
    assert mount_module._pick_local_port(28222) == 28222


def test_pick_local_port_auto_picks_available_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        occupied_port = listener.getsockname()[1]

        picked_port = mount_module._pick_local_port(0)

    assert picked_port > 0
    assert picked_port != occupied_port


def test_get_remote_path_prefers_target_dir() -> None:
    config = _make_config(target_dir="/inspire/hdd/project/demo", username="demo")

    assert mount_module._get_remote_path(config) == "/inspire/hdd/project/demo"


def test_get_remote_path_falls_back_to_username() -> None:
    config = _make_config(target_dir=None, username="demo")

    assert mount_module._get_remote_path(config) == "/inspire/hdd/global_user/demo"


def test_mount_start_cleans_up_created_tunnel_on_rclone_failure(monkeypatch) -> None:
    bridge = SimpleNamespace(name="demo", ssh_port=22, ssh_user="ubuntu", identity_file=None)
    killed_ports: list[int] = []

    monkeypatch.setattr(mount_module, "_check_rclone_installed", lambda: True)
    monkeypatch.setattr(
        mount_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(target_dir="/remote/path", username="demo"), {}),
    )
    monkeypatch.setattr(mount_module, "load_tunnel_config", lambda: SimpleNamespace())
    monkeypatch.setattr(mount_module, "_find_connected_bridge", lambda *a, **k: bridge)
    monkeypatch.setattr(mount_module, "_pick_local_port", lambda requested=0: 31337)
    monkeypatch.setattr(mount_module, "_find_tunnel_pid", lambda port: None)
    monkeypatch.setattr(mount_module, "_establish_tunnel", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_ensure_sftp_server", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_get_identity_file", lambda bridge: Path("/tmp/test-key"))
    monkeypatch.setattr(
        mount_module, "_kill_tunnel", lambda port: killed_ports.append(port) or True
    )
    monkeypatch.setattr(mount_module, "_remove_mount_state", lambda mount_point: None)
    monkeypatch.setattr(mount_module, "_is_mounted", lambda mount_point: False)
    monkeypatch.setattr(
        mount_module.tempfile,
        "NamedTemporaryFile",
        lambda **_: SimpleNamespace(name="/tmp/mount-start.log"),
    )
    monkeypatch.setattr(mount_module, "_tail_file", lambda *a, **k: "boom")
    monkeypatch.setattr(
        mount_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"),
    )

    runner = CliRunner()
    result = runner.invoke(mount_start, [])

    assert result.exit_code == mount_module.EXIT_GENERAL_ERROR
    assert "Mount failed: boom" in result.output
    assert killed_ports == [31337]


def test_mount_start_preserves_reused_tunnel_on_rclone_failure(monkeypatch) -> None:
    bridge = SimpleNamespace(name="demo", ssh_port=22, ssh_user="ubuntu", identity_file=None)
    killed_ports: list[int] = []

    monkeypatch.setattr(mount_module, "_check_rclone_installed", lambda: True)
    monkeypatch.setattr(
        mount_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(target_dir="/remote/path", username="demo"), {}),
    )
    monkeypatch.setattr(mount_module, "load_tunnel_config", lambda: SimpleNamespace())
    monkeypatch.setattr(mount_module, "_find_connected_bridge", lambda *a, **k: bridge)
    monkeypatch.setattr(mount_module, "_pick_local_port", lambda requested=0: 31337)
    monkeypatch.setattr(mount_module, "_find_tunnel_pid", lambda port: 4242)
    monkeypatch.setattr(mount_module, "_ensure_sftp_server", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_get_identity_file", lambda bridge: Path("/tmp/test-key"))
    monkeypatch.setattr(
        mount_module, "_kill_tunnel", lambda port: killed_ports.append(port) or True
    )
    monkeypatch.setattr(mount_module, "_remove_mount_state", lambda mount_point: None)
    monkeypatch.setattr(mount_module, "_is_mounted", lambda mount_point: False)
    monkeypatch.setattr(
        mount_module.tempfile,
        "NamedTemporaryFile",
        lambda **_: SimpleNamespace(name="/tmp/mount-start.log"),
    )
    monkeypatch.setattr(mount_module, "_tail_file", lambda *a, **k: "boom")
    monkeypatch.setattr(
        mount_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "boom"),
    )

    runner = CliRunner()
    result = runner.invoke(mount_start, [])

    assert result.exit_code == mount_module.EXIT_GENERAL_ERROR
    assert killed_ports == []


def test_mount_stop_all_cleans_up_state_on_unmount_failure(monkeypatch) -> None:
    state = {
        "/tmp/demo": {
            "mount_point": "/tmp/demo",
            "local_port": 31337,
            "bridge_name": "demo",
        }
    }
    removed: list[Path] = []
    killed: list[int] = []

    monkeypatch.setattr(mount_module, "_load_all_mount_states", lambda: dict(state))
    monkeypatch.setattr(mount_module, "unmount", lambda mount_point: False)
    monkeypatch.setattr(mount_module, "_kill_tunnel", lambda port: killed.append(port) or True)
    monkeypatch.setattr(
        mount_module, "_remove_mount_state", lambda mount_point: removed.append(mount_point)
    )

    runner = CliRunner()
    result = runner.invoke(mount_stop, ["--all"])

    assert result.exit_code == 0
    assert "cleaned up saved state" in result.output
    assert killed == [31337]
    assert removed == [Path("/tmp/demo")]


def test_mount_stop_requires_path_or_state(monkeypatch) -> None:
    monkeypatch.setattr(mount_module, "_is_mounted", lambda mount_point: False)
    monkeypatch.setattr(mount_module, "_load_mount_state", lambda mount_point: None)

    runner = CliRunner()
    result = runner.invoke(mount_stop, [])

    assert result.exit_code == mount_module.EXIT_CONFIG_ERROR
    assert "No mount point specified." in result.output


def test_mount_start_fails_when_daemon_mount_never_becomes_ready(monkeypatch) -> None:
    bridge = SimpleNamespace(name="demo", ssh_port=22, ssh_user="ubuntu", identity_file=None)
    cleaned: list[tuple[int, int | None]] = []

    monkeypatch.setattr(mount_module, "_check_rclone_installed", lambda: True)
    monkeypatch.setattr(
        mount_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(target_dir="/remote/path", username="demo"), {}),
    )
    monkeypatch.setattr(mount_module, "load_tunnel_config", lambda: SimpleNamespace())
    monkeypatch.setattr(mount_module, "_find_connected_bridge", lambda *a, **k: bridge)
    monkeypatch.setattr(mount_module, "_pick_local_port", lambda requested=0: 31337)
    monkeypatch.setattr(mount_module, "_find_tunnel_pid", lambda port: 4242)
    monkeypatch.setattr(mount_module, "_establish_tunnel", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_ensure_sftp_server", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_get_identity_file", lambda bridge: Path("/tmp/test-key"))
    monkeypatch.setattr(mount_module, "_is_mounted", lambda mount_point: False)
    monkeypatch.setattr(
        mount_module.tempfile,
        "NamedTemporaryFile",
        lambda **_: SimpleNamespace(name="/tmp/mount-start.log"),
    )
    monkeypatch.setattr(
        mount_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    monkeypatch.setattr(
        mount_module,
        "_wait_for_mount_ready",
        lambda mount_point, local_port, timeout=0: (False, 9999, "mountpoint is not mounted"),
    )
    monkeypatch.setattr(mount_module, "_tail_file", lambda *a, **k: "line1\nline2")
    monkeypatch.setattr(
        mount_module,
        "_cleanup_failed_mount_start",
        lambda **kwargs: cleaned.append((kwargs["local_port"], kwargs.get("rclone_pid"))),
    )

    runner = CliRunner()
    result = runner.invoke(mount_start, [])

    assert result.exit_code == mount_module.EXIT_GENERAL_ERROR
    assert "mountpoint is not mounted" in result.output
    assert "Recent rclone log" in result.output
    assert cleaned == [(31337, 9999)]


def test_mount_start_saves_rclone_pid_and_tuned_defaults(monkeypatch) -> None:
    bridge = SimpleNamespace(name="demo", ssh_port=22, ssh_user="ubuntu", identity_file=None)
    saved: dict[str, object] = {}
    commands: list[list[str]] = []

    monkeypatch.setattr(mount_module, "_check_rclone_installed", lambda: True)
    monkeypatch.setattr(
        mount_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(target_dir="/remote/path", username="demo"), {}),
    )
    monkeypatch.setattr(mount_module, "load_tunnel_config", lambda: SimpleNamespace())
    monkeypatch.setattr(mount_module, "_find_connected_bridge", lambda *a, **k: bridge)
    monkeypatch.setattr(mount_module, "_pick_local_port", lambda requested=0: 31337)
    monkeypatch.setattr(mount_module, "_find_tunnel_pid", lambda port: 4242)
    monkeypatch.setattr(mount_module, "_establish_tunnel", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_ensure_sftp_server", lambda *a, **k: True)
    monkeypatch.setattr(mount_module, "_get_identity_file", lambda bridge: Path("/tmp/test-key"))
    monkeypatch.setattr(mount_module, "_is_mounted", lambda mount_point: False)
    monkeypatch.setattr(
        mount_module.tempfile,
        "NamedTemporaryFile",
        lambda **_: SimpleNamespace(name="/tmp/mount-start.log"),
    )

    def fake_run(*args, **kwargs):
        commands.append(list(args[0]))
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(mount_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        mount_module,
        "_wait_for_mount_ready",
        lambda mount_point, local_port, timeout=0: (True, 5252, ""),
    )
    monkeypatch.setattr(
        mount_module,
        "_save_mount_state",
        lambda mount_point, local_port, bridge_name, tunnel_pid, rclone_pid: saved.update(
            {
                "mount_point": str(mount_point),
                "local_port": local_port,
                "bridge_name": bridge_name,
                "tunnel_pid": tunnel_pid,
                "rclone_pid": rclone_pid,
            }
        ),
    )

    runner = CliRunner()
    result = runner.invoke(mount_start, [])

    assert result.exit_code == 0
    assert saved == {
        "mount_point": str(mount_module.DEFAULT_MOUNT_POINT),
        "local_port": 31337,
        "bridge_name": "demo",
        "tunnel_pid": 4242,
        "rclone_pid": 5252,
    }
    rclone_cmd = commands[-1]
    assert "--dir-cache-time" in rclone_cmd
    assert mount_module.DEFAULT_DIR_CACHE_TIME in rclone_cmd
    assert "--attr-timeout" in rclone_cmd
    assert mount_module.DEFAULT_ATTR_TIMEOUT in rclone_cmd
    assert "--daemon-wait" in rclone_cmd
    assert mount_module.DEFAULT_DAEMON_WAIT in rclone_cmd
    assert "--log-file" in rclone_cmd


def test_mount_stop_all_kills_saved_rclone_pid(monkeypatch) -> None:
    state = {
        "/tmp/demo": {
            "mount_point": "/tmp/demo",
            "local_port": 31337,
            "bridge_name": "demo",
            "rclone_pid": 5252,
        }
    }
    removed: list[Path] = []
    killed_tunnels: list[int] = []
    killed_processes: list[int | None] = []

    monkeypatch.setattr(mount_module, "_load_all_mount_states", lambda: dict(state))
    monkeypatch.setattr(mount_module, "unmount", lambda mount_point: True)
    monkeypatch.setattr(
        mount_module, "_kill_tunnel", lambda port: killed_tunnels.append(port) or True
    )
    monkeypatch.setattr(
        mount_module, "_kill_process", lambda pid: killed_processes.append(pid) or True
    )
    monkeypatch.setattr(
        mount_module, "_remove_mount_state", lambda mount_point: removed.append(mount_point)
    )

    runner = CliRunner()
    result = runner.invoke(mount_stop, ["--all"])

    assert result.exit_code == 0
    assert killed_processes == [5252]
    assert killed_tunnels == [31337]
    assert removed == [Path("/tmp/demo")]


def test_mount_status_reports_unhealthy_mount(monkeypatch) -> None:
    state = {
        "mount_point": "/tmp/demo",
        "local_port": 31337,
        "bridge_name": "demo",
        "tunnel_pid": 4242,
        "rclone_pid": 5252,
    }
    monkeypatch.setattr(mount_module, "_load_mount_state", lambda mount_point: state)
    monkeypatch.setattr(
        mount_module,
        "_mount_health_details",
        lambda mount_point, state=None: (
            "unhealthy",
            "mounted but helper processes or filesystem access are unhealthy",
        ),
    )

    runner = CliRunner()
    result = runner.invoke(mount_status, ["/tmp/demo"])

    assert result.exit_code == 0
    assert "mounted but unhealthy" in result.output
