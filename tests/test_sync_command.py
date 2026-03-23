import importlib
from pathlib import Path
from typing import Any, Dict

from click.testing import CliRunner

from inspire.bridge.tunnel import BridgeProfile, TunnelConfig
from inspire.cli.context import EXIT_CONFIG_ERROR, EXIT_GENERAL_ERROR, EXIT_SUCCESS
from inspire.cli.main import main as cli_main
from inspire.config import Config

sync_cmd_module = importlib.import_module("inspire.cli.commands.sync")


def make_sync_config(
    tmp_path: Path,
    *,
    target_dir: str | None = "/remote/project",
    sync_bridge: str | None = None,
) -> Config:
    return Config(
        username="",
        password="",
        target_dir=target_dir,
        sync_bridge=sync_bridge,
        tunnel_retries=0,
        tunnel_retry_pause=0.0,
    )


def make_tunnel_config() -> TunnelConfig:
    tunnel_config = TunnelConfig()
    tunnel_config.add_bridge(
        BridgeProfile(
            name="cpu-main",
            proxy_url="https://cpu.example.com",
            has_internet=True,
        )
    )
    return tunnel_config


def test_sync_uses_current_working_directory_as_source_dir(monkeypatch, tmp_path: Path) -> None:
    config = make_sync_config(tmp_path, sync_bridge="cpu-main", target_dir="/remote/project")
    source_dir = tmp_path / "project"
    source_dir.mkdir()
    captured: Dict[str, Any] = {}

    monkeypatch.chdir(source_dir)
    monkeypatch.setattr(
        Config,
        "from_files_and_env",
        classmethod(lambda cls, require_target_dir=False, require_credentials=True: (config, {})),
    )
    monkeypatch.setattr(sync_cmd_module, "load_tunnel_config", make_tunnel_config)
    monkeypatch.setattr(sync_cmd_module, "is_tunnel_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        sync_cmd_module,
        "sync_via_rsync",
        lambda *args, **kwargs: captured.update(kwargs)
        or {"success": True, "bridge_name": "cpu-main"},
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["sync"])

    assert result.exit_code == EXIT_SUCCESS
    assert captured["source_dir"] == str(source_dir.resolve())


def test_sync_saves_source_dir_and_runs_rsync(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "local-src"
    source_dir.mkdir()
    config = make_sync_config(tmp_path, target_dir=None, sync_bridge=None)
    captured: Dict[str, Any] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        Config,
        "from_files_and_env",
        classmethod(lambda cls, require_target_dir=False, require_credentials=True: (config, {})),
    )
    monkeypatch.setattr(sync_cmd_module, "load_tunnel_config", make_tunnel_config)
    monkeypatch.setattr(sync_cmd_module, "is_tunnel_available", lambda *args, **kwargs: True)

    def fake_sync_via_rsync(*args: Any, **kwargs: Any) -> dict:
        captured.update(kwargs)
        return {"success": True, "bridge_name": kwargs["bridge_name"]}

    monkeypatch.setattr(sync_cmd_module, "sync_via_rsync", fake_sync_via_rsync)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["sync", "cpu-main", "/remote/project"])

    assert result.exit_code == EXIT_SUCCESS
    assert captured["source_dir"] == str(tmp_path.resolve())
    assert captured["target_dir"] == "/remote/project"
    assert captured["bridge_name"] == "cpu-main"
    assert captured["delete"] is False

    config_path = tmp_path / ".inspire" / "config.toml"
    content = config_path.read_text(encoding="utf-8")
    assert 'sync_bridge = "cpu-main"' in content
    assert 'target_dir = "/remote/project"' in content


def test_sync_uses_saved_source_dir_without_flag(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "saved-src"
    source_dir.mkdir()
    config = make_sync_config(tmp_path, sync_bridge="cpu-main", target_dir="/remote/project")
    captured: Dict[str, Any] = {}

    monkeypatch.chdir(source_dir)
    monkeypatch.setattr(
        Config,
        "from_files_and_env",
        classmethod(lambda cls, require_target_dir=False, require_credentials=True: (config, {})),
    )
    monkeypatch.setattr(sync_cmd_module, "load_tunnel_config", make_tunnel_config)
    monkeypatch.setattr(sync_cmd_module, "is_tunnel_available", lambda *args, **kwargs: True)

    def fake_sync_via_rsync(*args: Any, **kwargs: Any) -> dict:
        captured.update(kwargs)
        return {"success": True, "bridge_name": kwargs["bridge_name"]}

    monkeypatch.setattr(sync_cmd_module, "sync_via_rsync", fake_sync_via_rsync)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["sync"])

    assert result.exit_code == EXIT_SUCCESS
    assert captured["source_dir"] == str(source_dir.resolve())
    assert captured["delete"] is False


def test_sync_fails_when_no_bridge_is_reachable(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "saved-src"
    source_dir.mkdir()
    config = make_sync_config(tmp_path, sync_bridge="cpu-main", target_dir="/remote/project")

    monkeypatch.chdir(source_dir)
    monkeypatch.setattr(
        Config,
        "from_files_and_env",
        classmethod(lambda cls, require_target_dir=False, require_credentials=True: (config, {})),
    )
    monkeypatch.setattr(sync_cmd_module, "load_tunnel_config", make_tunnel_config)
    monkeypatch.setattr(sync_cmd_module, "is_tunnel_available", lambda *args, **kwargs: False)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["sync"])

    assert result.exit_code == EXIT_GENERAL_ERROR
    assert "ssh tunnel is not available" in result.output.lower()


def test_sync_requires_bridge_and_target_dir_on_first_run(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "saved-src"
    source_dir.mkdir()
    config = make_sync_config(tmp_path, target_dir=None, sync_bridge=None)

    monkeypatch.chdir(source_dir)
    monkeypatch.setattr(
        Config,
        "from_files_and_env",
        classmethod(lambda cls, require_target_dir=False, require_credentials=True: (config, {})),
    )
    monkeypatch.setattr(sync_cmd_module, "load_tunnel_config", make_tunnel_config)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["sync"])

    assert result.exit_code == EXIT_CONFIG_ERROR
    assert (
        "sync target directory" in result.output.lower() or "sync bridge" in result.output.lower()
    )
