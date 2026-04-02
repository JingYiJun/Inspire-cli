"""Tests for multi-workspace `resources list` scope resolution."""

from __future__ import annotations

import json
from types import SimpleNamespace

from click.testing import CliRunner

from inspire.cli.main import main as cli_main
from inspire.cli.commands.resources import resources_list as resources_list_module
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web.resources import ComputeGroupAvailability


def _patch_config(monkeypatch, config) -> None:  # noqa: ANN001
    def fake_from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ):  # type: ignore[override]  # noqa: ANN001,E501
        return config, {}

    monkeypatch.setattr(
        resources_list_module.Config,
        "from_files_and_env",
        classmethod(fake_from_files_and_env),
    )


def test_resources_list_defaults_to_configured_workspaces(monkeypatch) -> None:
    config = SimpleNamespace(
        workspaces={
            "cpu": "ws-cpu",
            "gpu": "ws-gpu",
            "internet": "ws-cpu",
        }
    )
    session = SimpleNamespace(
        workspace_id="ws-session",
        all_workspace_ids=["ws-session"],
        all_workspace_names={},
    )
    captured: dict[str, list[str] | None] = {}

    _patch_config(monkeypatch, config)
    monkeypatch.setattr(
        resources_list_module, "get_web_session", lambda require_workspace=False: session
    )
    monkeypatch.setattr(
        resources_list_module,
        "_collect_cpu_resources",
        lambda **kwargs: [
            resources_list_module.CPUResourceSummary(
                group_id="lcg-cpu",
                group_name="CPU资源",
                cpu_per_node_min=104,
                cpu_per_node_max=120,
                total_nodes=3,
                ready_nodes=1,
                free_nodes=0,
                has_cpu_specs=False,
                workspace_ids=["ws-cpu"],
                workspace_aliases=["cpu", "internet"],
            )
        ],
    )

    def fake_accurate(*, workspace_ids=None, **kwargs):  # noqa: ANN001, ANN003
        captured["workspace_ids"] = workspace_ids
        return [
            browser_api_module.GPUAvailability(
                group_id="lcg-cpu",
                group_name="CPU Group",
                gpu_type="CPU",
                total_gpus=0,
                used_gpus=0,
                available_gpus=0,
                low_priority_gpus=0,
                workspace_ids=["ws-cpu"],
            ),
            browser_api_module.GPUAvailability(
                group_id="lcg-demo",
                group_name="Demo Group",
                gpu_type="H200",
                total_gpus=8,
                used_gpus=0,
                available_gpus=8,
                low_priority_gpus=0,
                workspace_ids=["ws-cpu", "ws-gpu"],
            ),
        ]

    monkeypatch.setattr(
        resources_list_module.browser_api_module,
        "get_accurate_gpu_availability",
        fake_accurate,
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "resources", "list"])

    assert result.exit_code == 0
    assert captured["workspace_ids"] == ["ws-cpu", "ws-gpu"]

    payload = json.loads(result.output)
    assert [entry["group_id"] for entry in payload["data"]["availability"]] == ["lcg-demo"]
    entry = payload["data"]["availability"][0]
    assert entry["workspace_ids"] == ["ws-cpu", "ws-gpu"]
    assert entry["workspace_aliases"] == ["cpu", "internet", "gpu"]
    assert payload["data"]["cpu_resources"] == [
        {
            "group_id": "lcg-cpu",
            "group_name": "CPU资源",
            "workspace_ids": ["ws-cpu"],
            "workspace_aliases": ["cpu", "internet"],
            "cpu_per_node_min": 104,
            "cpu_per_node_max": 120,
            "total_cpu_cores": 0,
            "available_cpu_cores": 0,
            "spec_cpu_min": None,
            "spec_cpu_max": None,
            "spec_memory_gib_min": None,
            "spec_memory_gib_max": None,
            "total_nodes": 3,
            "ready_nodes": 1,
            "free_nodes": 0,
            "has_cpu_specs": False,
        }
    ]


def test_resources_list_summary_reuses_single_session(monkeypatch) -> None:
    config = SimpleNamespace(workspaces={"gpu": "ws-gpu"})
    session = SimpleNamespace(
        workspace_id="ws-gpu",
        all_workspace_ids=["ws-gpu"],
        all_workspace_names={},
    )
    calls = {"count": 0, "cpu_session": None}

    _patch_config(monkeypatch, config)

    def fake_get_web_session(require_workspace=False):  # noqa: ANN001
        calls["count"] += 1
        return session

    monkeypatch.setattr(resources_list_module, "get_web_session", fake_get_web_session)
    monkeypatch.setattr(
        resources_list_module.browser_api_module,
        "get_accurate_gpu_availability",
        lambda **kwargs: [],
    )

    def fake_collect_cpu_resources(**kwargs):  # noqa: ANN003
        calls["cpu_session"] = kwargs["session"]
        return []

    monkeypatch.setattr(
        resources_list_module,
        "_collect_cpu_resources",
        fake_collect_cpu_resources,
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "resources", "list"])

    assert result.exit_code == 0
    assert calls["count"] == 1
    assert calls["cpu_session"] is session


def test_resources_list_all_expands_to_all_accessible_workspaces(monkeypatch) -> None:
    config = SimpleNamespace(workspaces={"gpu": "ws-gpu"})
    session = SimpleNamespace(
        workspace_id="ws-gpu",
        all_workspace_ids=["ws-gpu", "ws-extra"],
        all_workspace_names={},
    )
    captured: dict[str, list[str] | None] = {}

    _patch_config(monkeypatch, config)
    monkeypatch.setattr(
        resources_list_module, "get_web_session", lambda require_workspace=False: session
    )
    monkeypatch.setattr(resources_list_module, "_collect_cpu_resources", lambda **kwargs: [])

    def fake_enumerate(session, workspace_id=None, base_url=None):  # noqa: ANN001, ANN201
        return [
            {"id": "ws-gpu", "name": "GPU"},
            {"id": "ws-extra-2", "name": "Extra 2"},
        ]

    monkeypatch.setattr(
        "inspire.platform.web.browser_api.workspaces.try_enumerate_workspaces",
        fake_enumerate,
    )

    def fake_accurate(*, workspace_ids=None, **kwargs):  # noqa: ANN001, ANN003
        captured["workspace_ids"] = workspace_ids
        return []

    monkeypatch.setattr(
        resources_list_module.browser_api_module,
        "get_accurate_gpu_availability",
        fake_accurate,
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "resources", "list", "--all"])

    assert result.exit_code == 0
    assert captured["workspace_ids"] == ["ws-gpu", "ws-extra", "ws-extra-2"]


def test_resources_list_nodes_view_aggregates_explicit_scope(monkeypatch) -> None:
    config = SimpleNamespace(workspaces={"gpu": "ws-gpu", "gpu4090_internet": "ws-4090"})
    session = SimpleNamespace(
        workspace_id="ws-session",
        all_workspace_ids=["ws-session"],
        all_workspace_names={},
    )
    captured: dict[str, object] = {}

    _patch_config(monkeypatch, config)
    monkeypatch.setattr(
        resources_list_module, "get_web_session", lambda require_workspace=False: session
    )
    monkeypatch.setattr(
        resources_list_module,
        "_collect_cpu_resources",
        lambda **kwargs: [
            resources_list_module.CPUResourceSummary(
                group_id="lcg-cpu-2",
                group_name="CPU资源-2",
                cpu_per_node_min=120,
                cpu_per_node_max=120,
                spec_cpu_min=1,
                spec_cpu_max=55,
                spec_memory_gib_min=4,
                spec_memory_gib_max=500,
                total_nodes=10,
                ready_nodes=6,
                free_nodes=1,
                has_cpu_specs=True,
                workspace_ids=["ws-gpu"],
                workspace_aliases=["gpu"],
            )
        ],
    )

    def fake_fetch_resource_availability(**kwargs):  # noqa: ANN003, ANN201
        captured["workspace_ids"] = kwargs["workspace_ids"]
        captured["workspace_aliases_by_id"] = kwargs["workspace_aliases_by_id"]
        return [
            ComputeGroupAvailability(
                group_id="lcg-demo",
                group_name="Demo Group",
                gpu_type="H200",
                gpu_per_node=8,
                total_nodes=1,
                ready_nodes=1,
                free_nodes=1,
                free_gpus=8,
                workspace_ids=["ws-gpu"],
                workspace_aliases=["gpu"],
            )
        ]

    monkeypatch.setattr(
        resources_list_module,
        "fetch_resource_availability",
        fake_fetch_resource_availability,
    )

    runner = CliRunner()
    result = runner.invoke(cli_main, ["--json", "resources", "list", "--view", "nodes"])

    assert result.exit_code == 0
    assert captured["workspace_ids"] == ["ws-gpu", "ws-4090"]
    assert captured["workspace_aliases_by_id"] == {
        "ws-gpu": ["gpu"],
        "ws-4090": ["gpu4090_internet"],
    }

    payload = json.loads(result.output)
    entry = payload["data"]["availability"][0]
    assert entry["workspace_ids"] == ["ws-gpu"]
    assert entry["workspace_aliases"] == ["gpu"]
    assert payload["data"]["cpu_resources"][0]["group_id"] == "lcg-cpu-2"
    assert payload["data"]["cpu_resources"][0]["has_cpu_specs"] is True


def test_resources_list_workspace_flag_removed() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["resources", "list", "--workspace"])

    assert result.exit_code != 0
    assert "No such option: --workspace" in result.output


def test_render_gpu_summary_section_disambiguates_duplicate_names() -> None:
    availability = [
        browser_api_module.GPUAvailability(
            group_id="lcg-11111111",
            group_name="CQ-科研驾驶舱",
            gpu_type="NVIDIA H200 (141GB)",
            total_gpus=32,
            used_gpus=7,
            available_gpus=25,
            low_priority_gpus=0,
        ),
        browser_api_module.GPUAvailability(
            group_id="lcg-22222222",
            group_name="CQ-科研驾驶舱",
            gpu_type="NVIDIA H200 (141GB)",
            total_gpus=32,
            used_gpus=19,
            available_gpus=13,
            low_priority_gpus=0,
        ),
    ]

    lines = resources_list_module._render_gpu_summary_section(availability)

    assert any("CQ-科研驾驶舱 [11111111]" in line for line in lines)
    assert any("CQ-科研驾驶舱 [22222222]" in line for line in lines)


def test_render_table_keeps_cjk_rows_aligned() -> None:
    lines = resources_list_module._render_table(
        [("Compute Group", "left"), ("Available", "right"), ("Total", "right")],
        [
            {"Compute Group": "CQ-科研驾驶舱 [11111111]", "Available": "25", "Total": "544"},
            {"Compute Group": "cuda12.8版本H100", "Available": "-3", "Total": "2359"},
        ],
    )

    data_lines = [line for line in lines if line and not set(line) == {"─"}]
    widths = [resources_list_module._display_width(line) for line in data_lines]
    assert len(set(widths)) == 1
