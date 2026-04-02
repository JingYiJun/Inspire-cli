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


def test_is_tunnel_available_with_retries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Test that is_tunnel_available retries on failure."""
    config = TunnelConfig()
    fake_rtunnel = tmp_path / "rtunnel"
    fake_rtunnel.write_text("#!/bin/sh\nexit 0\n")
    fake_rtunnel.chmod(0o755)

    bridge = BridgeProfile(name="test-bridge", proxy_url="https://proxy.example.com/proxy/31337/")
    config.add_bridge(bridge)

    monkeypatch.setattr(ssh_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    attempt_count = 0

    def fake_test_connection(bridge, config, timeout):
        nonlocal attempt_count
        attempt_count += 1
        # Succeed on third attempt
        return attempt_count >= 3

    monkeypatch.setattr(ssh_module, "_test_ssh_connection", fake_test_connection)

    # Mock time.sleep to avoid actual delays
    monkeypatch.setattr(ssh_module.time, "sleep", lambda x: None)

    result = ssh_module.is_tunnel_available(
        bridge_name="test-bridge",
        config=config,
        retries=3,
        retry_pause=0.1,
        progressive=False,
    )

    assert result is True
    assert attempt_count == 3


def test_is_tunnel_available_exhausted_returns_false(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Test that is_tunnel_available returns False when all retries exhausted."""
    config = TunnelConfig()
    fake_rtunnel = tmp_path / "rtunnel"
    fake_rtunnel.write_text("#!/bin/sh\nexit 0\n")
    fake_rtunnel.chmod(0o755)

    bridge = BridgeProfile(name="test-bridge", proxy_url="https://proxy.example.com/proxy/31337/")
    config.add_bridge(bridge)

    monkeypatch.setattr(ssh_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    # Always fail
    monkeypatch.setattr(ssh_module, "_test_ssh_connection", lambda bridge, config, timeout: False)
    monkeypatch.setattr(ssh_module.time, "sleep", lambda x: None)

    result = ssh_module.is_tunnel_available(
        bridge_name="test-bridge",
        config=config,
        retries=2,
        retry_pause=0.1,
    )

    assert result is False


def test_generate_ssh_config_with_identity_file(
    tmp_path: Path,
) -> None:
    """Test SSH config generation includes identity file when set."""
    bridge = BridgeProfile(
        name="test-bridge",
        proxy_url="https://proxy.example.com/proxy/31337/",
        identity_file="/path/to/key.pem",
    )

    config = ssh_module.generate_ssh_config(bridge, tmp_path / "rtunnel")

    assert "IdentityFile /path/to/key.pem" in config


def test_generate_ssh_config_without_identity_file(
    tmp_path: Path,
) -> None:
    """Test SSH config generation excludes identity file when not set."""
    bridge = BridgeProfile(
        name="test-bridge",
        proxy_url="https://proxy.example.com/proxy/31337/",
        identity_file=None,
    )

    config = ssh_module.generate_ssh_config(bridge, tmp_path / "rtunnel")

    assert "IdentityFile" not in config


def test_generate_all_ssh_configs_sorted_alphabetically(
    tmp_path: Path,
) -> None:
    """Test that generate_all_ssh_configs sorts bridges alphabetically."""
    config = TunnelConfig()

    fake_rtunnel = tmp_path / "rtunnel"
    fake_rtunnel.write_text("#!/bin/sh\nexit 0\n")
    fake_rtunnel.chmod(0o755)

    # Add bridges in non-alphabetical order
    config.add_bridge(BridgeProfile(name="zebra", proxy_url="https://proxy.example.com/proxy/1/"))
    config.add_bridge(BridgeProfile(name="alpha", proxy_url="https://proxy.example.com/proxy/2/"))
    config.add_bridge(BridgeProfile(name="beta", proxy_url="https://proxy.example.com/proxy/3/"))

    all_configs = ssh_module.generate_all_ssh_configs(config)

    # Check alphabetical ordering
    zebra_pos = all_configs.find("Host zebra")
    alpha_pos = all_configs.find("Host alpha")
    beta_pos = all_configs.find("Host beta")

    assert alpha_pos < beta_pos < zebra_pos


def test_install_ssh_config_updates_existing_host(
    tmp_path: Path,
) -> None:
    """Test that install_ssh_config updates existing host entry."""
    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(mode=0o700, parents=True)

    existing_config = """Host other
    HostName other.example.com

Host mybridge
    HostName old.example.com
    User olduser
"""
    ssh_config_path.write_text(existing_config)

    new_config = """Host mybridge
    HostName localhost
    User root
"""

    # Temporarily override HOME to use our temp directory
    import os

    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)

    try:
        result = ssh_module.install_ssh_config(new_config, "mybridge")

        assert result["success"] is True
        assert result["updated"] is True

        updated_content = ssh_config_path.read_text()
        assert "localhost" in updated_content
        assert "old.example.com" not in updated_content
        assert "other.example.com" in updated_content  # Other hosts preserved
    finally:
        if old_home:
            os.environ["HOME"] = old_home


def test_install_ssh_config_adds_new_host(
    tmp_path: Path,
) -> None:
    """Test that install_ssh_config adds new host when not existing."""
    ssh_config_path = tmp_path / ".ssh" / "config"
    ssh_config_path.parent.mkdir(mode=0o700, parents=True)

    existing_config = """Host existing
    HostName existing.example.com
"""
    ssh_config_path.write_text(existing_config)

    new_config = """Host newbridge
    HostName localhost
    User root
"""

    import os

    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)

    try:
        result = ssh_module.install_ssh_config(new_config, "newbridge")

        assert result["success"] is True
        assert result["updated"] is False

        updated_content = ssh_config_path.read_text()
        assert "newbridge" in updated_content
        assert "existing" in updated_content
    finally:
        if old_home:
            os.environ["HOME"] = old_home


def test_ssh_connection_timeout_logged(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    """Test that SSH timeout errors are logged at debug level."""
    config = TunnelConfig()
    fake_rtunnel = tmp_path / "rtunnel"
    fake_rtunnel.write_text("#!/bin/sh\nexit 0\n")
    fake_rtunnel.chmod(0o755)

    bridge = BridgeProfile(name="test-bridge", proxy_url="https://proxy.example.com/proxy/31337/")
    config.add_bridge(bridge)

    # Mock _ensure_rtunnel_binary in both modules
    monkeypatch.setattr(ssh_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    import logging

    with caplog.at_level(logging.DEBUG, logger="inspire.bridge.tunnel.ssh"):
        result = ssh_module._test_ssh_connection(bridge=bridge, config=config)

    assert result is False
    assert "timed out" in caplog.text.lower() or "timeout" in caplog.text.lower()


def test_ssh_connection_filenotfound_logged(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    """Test that SSH FileNotFoundError is logged at debug level."""
    config = TunnelConfig()
    fake_rtunnel = tmp_path / "rtunnel"
    fake_rtunnel.write_text("#!/bin/sh\nexit 0\n")
    fake_rtunnel.chmod(0o755)

    bridge = BridgeProfile(name="test-bridge", proxy_url="https://proxy.example.com/proxy/31337/")
    config.add_bridge(bridge)

    # Mock _ensure_rtunnel_binary in both modules
    monkeypatch.setattr(ssh_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_ensure_rtunnel_binary", lambda cfg: fake_rtunnel)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("ssh not found")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    import logging

    with caplog.at_level(logging.DEBUG, logger="inspire.bridge.tunnel.ssh"):
        result = ssh_module._test_ssh_connection(bridge=bridge, config=config)

    assert result is False
    assert "not found" in caplog.text.lower()


def test_split_ssh_config_blocks_parses_correctly() -> None:
    """Test that _split_ssh_config_blocks correctly splits host and raw blocks."""
    config = """# Comment line
Host server1
    HostName server1.example.com

Host server2
    HostName server2.example.com

# Another comment
"""
    blocks = ssh_module._split_ssh_config_blocks(config)

    # Trailing comments are combined with the last host block
    assert len(blocks) == 3
    assert blocks[0] == ("raw", "# Comment line\n")
    assert blocks[1][0] == "host"
    assert "server1" in blocks[1][1]
    assert blocks[2][0] == "host"
    assert "server2" in blocks[2][1]
    assert "# Another comment" in blocks[2][1]


def test_host_aliases_from_block_extracts_aliases() -> None:
    """Test that _host_aliases_from_block extracts all aliases."""
    block = """Host alias1 alias2 alias3
    HostName example.com
"""
    aliases = ssh_module._host_aliases_from_block(block)

    assert aliases == ["alias1", "alias2", "alias3"]


def test_is_generated_inspire_host_block_detects_generated() -> None:
    """Test detection of Inspire-generated host blocks."""
    generated_block = """Host test
    HostName localhost
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
    ProxyCommand rtunnel
"""
    assert ssh_module._is_generated_inspire_host_block(generated_block) is True


def test_is_generated_inspire_host_block_rejects_custom() -> None:
    """Test that non-Inspire blocks are not detected as generated."""
    custom_block = """Host test
    HostName example.com
    User myuser
"""
    assert ssh_module._is_generated_inspire_host_block(custom_block) is False
