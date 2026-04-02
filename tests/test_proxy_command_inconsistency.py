"""Tests verifying get_ssh_command_args and _test_ssh_connection use consistent ProxyCommand.

After commit 215d3cd, _test_ssh_connection was switched to stdio:// ProxyCommand
but get_ssh_command_args was left using the old listener shell.  This caused
provisioning failures where is_tunnel_available succeeded but --command execution
failed with "Failed to start local rtunnel listener".

Both paths now use stdio:// ProxyCommand via _get_proxy_command.
"""

from __future__ import annotations

from pathlib import Path

from inspire.bridge.tunnel.models import BridgeProfile, TunnelConfig
from inspire.bridge.tunnel.ssh import _get_proxy_command
from inspire.bridge.tunnel.ssh_exec import get_ssh_command_args


def _make_config(tmp_path: Path) -> tuple[TunnelConfig, Path]:
    rtunnel_bin = tmp_path / ".local" / "bin" / "rtunnel"
    rtunnel_bin.parent.mkdir(parents=True, exist_ok=True)
    rtunnel_bin.write_text("#!/bin/sh\necho rtunnel\n")
    rtunnel_bin.chmod(0o755)

    config = TunnelConfig()
    config.add_bridge(
        BridgeProfile(
            name="test-bridge",
            proxy_url="https://proxy.example.com/ws/cpu/proxy/31337/",
            ssh_user="root",
            ssh_port=22222,
        )
    )
    return config, rtunnel_bin


class TestConsistentProxyCommandPaths:
    """Both get_ssh_command_args and _test_ssh_connection must use stdio://."""

    def test_get_ssh_command_args_uses_stdio_proxycommand(self, tmp_path: Path) -> None:
        config, rtunnel_bin = _make_config(tmp_path)

        args = get_ssh_command_args(
            bridge_name="test-bridge",
            config=config,
            remote_command="echo tunnel_ready",
        )

        joined = " ".join(args)
        assert "stdio://" in joined, "Should use stdio:// ProxyCommand"
        assert "pick_port" not in joined, "Should NOT use old listener"
        assert "LOCAL_PORT" not in joined, "Should NOT use old listener"
        assert "Failed to start local rtunnel listener" not in joined

    def test_get_proxy_command_uses_stdio(self, tmp_path: Path) -> None:
        config, rtunnel_bin = _make_config(tmp_path)
        bridge = config.get_bridge("test-bridge")

        proxy_cmd = _get_proxy_command(bridge, rtunnel_bin)

        assert "stdio://" in proxy_cmd
        assert "pick_port" not in proxy_cmd
        assert "LOCAL_PORT" not in proxy_cmd

    def test_both_paths_use_same_proxy_mechanism(self, tmp_path: Path) -> None:
        config, rtunnel_bin = _make_config(tmp_path)
        bridge = config.get_bridge("test-bridge")

        proxy_cmd = _get_proxy_command(bridge, rtunnel_bin)

        args = get_ssh_command_args(
            bridge_name="test-bridge",
            config=config,
            remote_command="echo ok",
        )
        joined = " ".join(args)

        proxy_uses_stdio = "stdio://" in proxy_cmd
        args_uses_stdio = "stdio://" in joined

        assert proxy_uses_stdio == args_uses_stdio, (
            f"Inconsistent ProxyCommand: _test_ssh_connection uses "
            f"{'stdio://' if proxy_uses_stdio else 'listener'}, "
            f"get_ssh_command_args uses "
            f"{'stdio://' if args_uses_stdio else 'listener'}"
        )


class TestProvisioningCommandPath:
    """Verify the --command provisioning path generates correct SSH args."""

    def test_command_path_returns_direct_ssh_args(self, tmp_path: Path) -> None:
        config, rtunnel_bin = _make_config(tmp_path)

        args = get_ssh_command_args(
            bridge_name="test-bridge",
            config=config,
            remote_command="echo tunnel_ready",
        )

        assert args[0] == "ssh"
        assert "ProxyCommand=" in " ".join(args)
        assert "stdio://" in " ".join(args)

    def test_test_connection_path_uses_direct_proxycommand(self, tmp_path: Path) -> None:
        config, rtunnel_bin = _make_config(tmp_path)
        bridge = config.get_bridge("test-bridge")

        proxy_cmd = _get_proxy_command(bridge, rtunnel_bin, quiet=True)

        assert proxy_cmd.startswith("sh -c")
        assert "stdio://" in proxy_cmd
        assert "pick_port" not in proxy_cmd
        assert "Failed to start" not in proxy_cmd
