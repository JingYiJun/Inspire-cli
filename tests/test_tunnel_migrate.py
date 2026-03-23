from __future__ import annotations

from click.testing import CliRunner

from inspire.bridge.tunnel import BridgeProfile, TunnelConfig
from inspire.cli.commands.tunnel import migrate as migrate_module
from inspire.cli.main import main as cli_main


def test_tunnel_migrate_merges_legacy_duplicate_into_named_bridge(
    monkeypatch,
) -> None:
    config = TunnelConfig()
    config.add_bridge(
        BridgeProfile(
            name="qz-cpu",
            proxy_url="https://proxy.example/cpu",
            notebook_id="0976b604-9309-4035-8a6a-16e94610a287",
            notebook_name="qz-cpu",
        )
    )
    config.add_bridge(
        BridgeProfile(
            name="notebook-0976b604",
            proxy_url="https://proxy.example/cpu",
            notebook_id="0976b604-9309-4035-8a6a-16e94610a287",
        )
    )

    saved: dict[str, TunnelConfig] = {}
    monkeypatch.setattr(migrate_module, "load_tunnel_config", lambda: config)
    monkeypatch.setattr(
        migrate_module,
        "save_tunnel_config",
        lambda updated: saved.__setitem__("config", updated),
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["tunnel", "migrate"])

    assert result.exit_code == 0
    assert "notebook-0976b604" in result.output
    updated = saved["config"]
    assert list(updated.bridges.keys()) == ["qz-cpu"]
    assert updated.bridges["qz-cpu"].aliases == ["notebook-0976b604"]


def test_tunnel_migrate_renames_legacy_bridge_to_notebook_name(monkeypatch) -> None:
    config = TunnelConfig()
    config.add_bridge(
        BridgeProfile(
            name="notebook-0bd0e099",
            proxy_url="https://proxy.example/h200",
            notebook_id="0bd0e099-b927-4e54-a319-151d5cc37662",
            notebook_name="kchen dev copy 8xh200",
        )
    )

    saved: dict[str, TunnelConfig] = {}
    monkeypatch.setattr(migrate_module, "load_tunnel_config", lambda: config)
    monkeypatch.setattr(
        migrate_module,
        "save_tunnel_config",
        lambda updated: saved.__setitem__("config", updated),
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["tunnel", "migrate"])

    assert result.exit_code == 0
    updated = saved["config"]
    assert "kchen-dev-copy-8xh200" in updated.bridges
    assert updated.default_bridge == "kchen-dev-copy-8xh200"
    assert updated.bridges["kchen-dev-copy-8xh200"].aliases == ["notebook-0bd0e099"]
