"""Tests for tunnel configuration loading and saving."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from inspire.bridge.tunnel.config import (
    _candidate_config_paths,
    _resolve_tunnel_account,
    load_tunnel_config,
    save_tunnel_config,
)
from inspire.bridge.tunnel.models import BridgeProfile, TunnelConfig


def test_load_tunnel_config_prefers_account_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test that account-specific file is preferred over shared file."""
    config_dir = tmp_path / ".inspire"
    config_dir.mkdir()

    # Create account-specific config
    account_config = config_dir / "bridges-testuser.json"
    account_data = {
        "default": "account-bridge",
        "bridges": [
            {
                "name": "account-bridge",
                "proxy_url": "https://proxy.example.com/proxy/12345/",
                "ssh_user": "root",
                "ssh_port": 22222,
            }
        ],
    }
    account_config.write_text(json.dumps(account_data))

    # Create shared config
    shared_config = config_dir / "bridges.json"
    shared_data = {
        "default": "shared-bridge",
        "bridges": [
            {
                "name": "shared-bridge",
                "proxy_url": "https://proxy.example.com/proxy/54321/",
                "ssh_user": "root",
                "ssh_port": 22222,
            }
        ],
    }
    shared_config.write_text(json.dumps(shared_data))

    monkeypatch.setenv("INSPIRE_BRIDGE_ACCOUNT", "testuser")

    config = load_tunnel_config(config_dir=config_dir)

    # Should use account bridge as default
    assert config.default_bridge == "account-bridge"
    assert "account-bridge" in config.bridges


def test_load_tunnel_config_merges_fallback_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test that fallback files are merged when account file is missing bridges."""
    config_dir = tmp_path / ".inspire"
    config_dir.mkdir()

    # Create account-specific config with one bridge
    account_config = config_dir / "bridges-testuser.json"
    account_data = {
        "default": "account-bridge",
        "bridges": [
            {
                "name": "account-bridge",
                "proxy_url": "https://proxy.example.com/proxy/12345/",
                "ssh_user": "root",
                "ssh_port": 22222,
            }
        ],
    }
    account_config.write_text(json.dumps(account_data))

    # Create shared config with different bridge
    shared_config = config_dir / "bridges.json"
    shared_data = {
        "bridges": [
            {
                "name": "shared-bridge",
                "proxy_url": "https://proxy.example.com/proxy/54321/",
                "ssh_user": "root",
                "ssh_port": 22222,
            }
        ],
    }
    shared_config.write_text(json.dumps(shared_data))

    monkeypatch.setenv("INSPIRE_BRIDGE_ACCOUNT", "testuser")

    config = load_tunnel_config(config_dir=config_dir)

    # Should have both bridges
    assert "account-bridge" in config.bridges
    assert "shared-bridge" in config.bridges


def test_save_tunnel_config_writes_correct_format(
    tmp_path: Path,
) -> None:
    """Test that save_tunnel_config writes the expected JSON format."""
    config_dir = tmp_path / ".inspire"
    config = TunnelConfig(config_dir=config_dir)
    config.account = "testuser"

    bridge = BridgeProfile(
        name="test-bridge",
        proxy_url="https://proxy.example.com/proxy/31337/",
        ssh_user="root",
        ssh_port=22222,
    )
    config.add_bridge(bridge)
    config.default_bridge = "test-bridge"

    save_tunnel_config(config)

    config_file = config_dir / "bridges-testuser.json"
    assert config_file.exists()

    data = json.loads(config_file.read_text())
    assert data["default"] == "test-bridge"
    assert len(data["bridges"]) == 1
    assert data["bridges"][0]["name"] == "test-bridge"
    assert data["bridges"][0]["proxy_url"] == "https://proxy.example.com/proxy/31337/"


def test_load_tunnel_config_migrates_old_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test migration from old tunnel.conf format."""
    config_dir = tmp_path / ".inspire"
    config_dir.mkdir()

    # Create old-style config
    old_config = config_dir / "tunnel.conf"
    old_config.write_text(
        """
# Old config format
PROXY_URL=https://proxy.example.com/proxy/31337/
SSH_USER=root
"""
    )

    config = load_tunnel_config(config_dir=config_dir)

    # Should have migrated to new format
    assert "default" in config.bridges
    assert config.bridges["default"].proxy_url == "https://proxy.example.com/proxy/31337/"
    assert config.bridges["default"].ssh_user == "root"


def test_candidate_config_paths_order() -> None:
    """Test that _candidate_config_paths returns paths in correct precedence order."""
    config_dir = Path("/tmp/test")

    paths = _candidate_config_paths(config_dir, "testuser")

    # Should have account-specific path first
    assert paths[0] == config_dir / "bridges-testuser.json"
    # Then legacy shared path
    assert paths[-1] == config_dir / "bridges.json"


def test_resolve_tunnel_account_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test account resolution priority order."""
    # Explicit parameter should win
    assert _resolve_tunnel_account("explicit") == "explicit"

    # Then env var
    monkeypatch.setenv("INSPIRE_BRIDGE_ACCOUNT", "env-account")
    assert _resolve_tunnel_account(None) == "env-account"

    # Clean up
    monkeypatch.delenv("INSPIRE_BRIDGE_ACCOUNT")


def test_load_tunnel_config_handles_corrupt_json(
    tmp_path: Path,
) -> None:
    """Test that corrupt JSON files are gracefully skipped."""
    config_dir = tmp_path / ".inspire"
    config_dir.mkdir()

    # Create corrupt JSON
    corrupt_config = config_dir / "bridges.json"
    corrupt_config.write_text("not valid json {{")

    # Should not raise exception
    config = load_tunnel_config(config_dir=config_dir)
    assert config.bridges == {}


def test_load_tunnel_config_handles_missing_bridge_fields(
    tmp_path: Path,
) -> None:
    """Test that bridges with missing required fields are skipped."""
    config_dir = tmp_path / ".inspire"
    config_dir.mkdir()

    config_file = config_dir / "bridges.json"
    data = {
        "bridges": [
            {
                "name": "valid-bridge",
                "proxy_url": "https://proxy.example.com/proxy/12345/",
            },
            {
                "name": "invalid-bridge",
                # Missing proxy_url
            },
        ],
    }
    config_file.write_text(json.dumps(data))

    config = load_tunnel_config(config_dir=config_dir)

    # Only valid bridge should be loaded
    assert "valid-bridge" in config.bridges
    assert "invalid-bridge" not in config.bridges


def test_bridge_profile_from_dict_persists_all_fields() -> None:
    """Test that BridgeProfile.from_dict preserves all fields."""
    data = {
        "name": "test",
        "proxy_url": "https://proxy.example.com/proxy/31337/",
        "ssh_user": "customuser",
        "ssh_port": 22223,
        "has_internet": False,
        "identity_file": "/path/to/key",
        "notebook_id": "nb-12345",
        "rtunnel_port": 31337,
    }

    profile = BridgeProfile.from_dict(data)

    assert profile.name == "test"
    assert profile.proxy_url == "https://proxy.example.com/proxy/31337/"
    assert profile.ssh_user == "customuser"
    assert profile.ssh_port == 22223
    assert profile.has_internet is False
    assert profile.identity_file == "/path/to/key"
    assert profile.notebook_id == "nb-12345"
    assert profile.rtunnel_port == 31337


def test_bridge_profile_to_dict_roundtrip() -> None:
    """Test that BridgeProfile round-trips through to_dict/from_dict."""
    original = BridgeProfile(
        name="test",
        proxy_url="https://proxy.example.com/proxy/31337/",
        ssh_user="root",
        ssh_port=22222,
        has_internet=True,
        identity_file="/path/to/key",
        notebook_id="nb-12345",
        rtunnel_port=31337,
    )

    data = original.to_dict()
    restored = BridgeProfile.from_dict(data)

    assert restored.name == original.name
    assert restored.proxy_url == original.proxy_url
    assert restored.ssh_user == original.ssh_user
    assert restored.ssh_port == original.ssh_port
    assert restored.has_internet == original.has_internet
    assert restored.identity_file == original.identity_file
    assert restored.notebook_id == original.notebook_id
    assert restored.rtunnel_port == original.rtunnel_port
