"""Tests for CPU compute group discovery, filtering, and selection fixes."""

from __future__ import annotations

from unittest.mock import patch

from inspire.cli.commands.notebook import notebook_create_flow as flow_module
from inspire.cli.commands.init.discover import _merge_compute_groups
from inspire.cli.commands.notebook.notebook_create_flow import _match_cpu_only_compute_group


# ---------------------------------------------------------------------------
# _merge_compute_groups: workspace_ids union
# ---------------------------------------------------------------------------


def test_merge_compute_groups_unions_workspace_ids() -> None:
    existing = [{"id": "g1", "name": "CPU", "workspace_ids": ["ws-a"]}]
    discovered = [{"id": "g1", "name": "CPU", "workspace_ids": ["ws-b"]}]
    result = _merge_compute_groups(existing, discovered)
    assert len(result) == 1
    assert result[0]["workspace_ids"] == ["ws-a", "ws-b"]


def test_merge_compute_groups_deduplicates_workspace_ids() -> None:
    existing = [{"id": "g1", "name": "CPU", "workspace_ids": ["ws-a", "ws-b"]}]
    discovered = [{"id": "g1", "name": "CPU", "workspace_ids": ["ws-b", "ws-c"]}]
    result = _merge_compute_groups(existing, discovered)
    assert result[0]["workspace_ids"] == ["ws-a", "ws-b", "ws-c"]


def test_merge_compute_groups_existing_has_no_workspace_ids() -> None:
    existing = [{"id": "g1", "name": "CPU"}]
    discovered = [{"id": "g1", "name": "CPU", "workspace_ids": ["ws-a"]}]
    result = _merge_compute_groups(existing, discovered)
    assert result[0]["workspace_ids"] == ["ws-a"]


# ---------------------------------------------------------------------------
# _config_compute_groups_fallback: workspace filtering
# ---------------------------------------------------------------------------


def test_config_compute_groups_fallback_filters_by_workspace() -> None:
    from inspire.platform.web.browser_api.notebooks import _config_compute_groups_fallback

    class FakeConfig:
        compute_groups = [
            {"id": "g1", "name": "GPU Group", "gpu_type": "H200", "workspace_ids": ["ws-gpu"]},
            {"id": "g2", "name": "CPU Group", "gpu_type": "", "workspace_ids": ["ws-cpu"]},
            {
                "id": "g3",
                "name": "Shared",
                "gpu_type": "A100",
                "workspace_ids": ["ws-gpu", "ws-cpu"],
            },
        ]

    with patch(
        "inspire.platform.web.browser_api.notebooks.Config.from_files_and_env",
        return_value=(FakeConfig(), {}),
    ):
        result = _config_compute_groups_fallback(workspace_id="ws-cpu")

    names = [g["name"] for g in result]
    assert "GPU Group" not in names
    assert "CPU Group" in names
    assert "Shared" in names


def test_config_compute_groups_fallback_no_filter_when_no_workspace() -> None:
    from inspire.platform.web.browser_api.notebooks import _config_compute_groups_fallback

    class FakeConfig:
        compute_groups = [
            {"id": "g1", "name": "GPU Group", "gpu_type": "H200", "workspace_ids": ["ws-gpu"]},
            {"id": "g2", "name": "CPU Group", "gpu_type": ""},
        ]

    with patch(
        "inspire.platform.web.browser_api.notebooks.Config.from_files_and_env",
        return_value=(FakeConfig(), {}),
    ):
        result = _config_compute_groups_fallback(workspace_id=None)

    assert len(result) == 2


def test_config_compute_groups_fallback_skips_group_with_missing_workspace_ids() -> None:
    from inspire.platform.web.browser_api.notebooks import _config_compute_groups_fallback

    class FakeConfig:
        compute_groups = [
            {"id": "g1", "name": "CPU Group", "gpu_type": ""},
        ]

    with patch(
        "inspire.platform.web.browser_api.notebooks.Config.from_files_and_env",
        return_value=(FakeConfig(), {}),
    ):
        result = _config_compute_groups_fallback(workspace_id="ws-any")

    assert result == []


# ---------------------------------------------------------------------------
# _match_cpu_only_compute_group: fail closed unless CPU capability is probed
# ---------------------------------------------------------------------------


def test_match_cpu_only_returns_none_without_probe_context() -> None:
    groups = [
        {"name": "语音项目测试专用", "gpu_type_stats": []},
        {"name": "CPU资源", "gpu_type_stats": []},
    ]
    group, _ = _match_cpu_only_compute_group(groups)
    assert group is None


def test_match_cpu_only_returns_none_without_session() -> None:
    groups = [
        {"name": "语音项目测试专用", "gpu_type_stats": []},
        {"name": "其他资源", "gpu_type_stats": []},
    ]
    group, _ = _match_cpu_only_compute_group(groups, workspace_id="ws-cpu")
    assert group is None


def test_match_cpu_only_returns_none_when_requested_cpu_count_is_unavailable() -> None:
    groups = [
        {
            "logic_compute_group_id": "g-cpu-2",
            "name": "CPU资源-2",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [],
        },
    ]

    with patch.object(
        flow_module.browser_api_module,
        "get_resource_prices",
        return_value=[{"gpu_count": 0, "cpu_count": 4}],
    ):
        group, _ = _match_cpu_only_compute_group(
            groups,
            workspace_id="ws-cpu",
            session=object(),
            requested_cpu_count=55,
        )

    assert group is None


def test_match_cpu_only_accepts_gpu_tagged_group_with_cpu_resources() -> None:
    groups = [
        {
            "logic_compute_group_id": "g-h200",
            "name": "H200-1号机房",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [{"gpu_info": {"gpu_type": "H200"}}],
        },
        {
            "logic_compute_group_id": "g-empty",
            "name": "CPU资源",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [],
        },
    ]

    with patch.object(
        flow_module.browser_api_module,
        "get_resource_prices",
        side_effect=lambda workspace_id, logic_compute_group_id, session=None: (
            [{"gpu_count": 0}] if logic_compute_group_id == "g-h200" else []
        ),
    ):
        group, _ = _match_cpu_only_compute_group(groups, workspace_id="ws-cpu", session=object())

    assert group is not None
    assert group["name"] == "H200-1号机房"


def test_match_cpu_only_prefers_group_with_more_free_cpu_nodes() -> None:
    groups = [
        {
            "logic_compute_group_id": "g-hpc",
            "name": "HPC-可上网区资源-2",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [],
        },
        {
            "logic_compute_group_id": "g-cpu-2",
            "name": "CPU资源-2",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [],
        },
    ]

    with (
        patch.object(
            flow_module.web_session_module,
            "fetch_workspace_availability",
            return_value=[
                {
                    "logic_compute_group_id": "g-hpc",
                    "gpu_count": 0,
                    "status": "READY",
                    "task_list": [],
                    "cordon_type": "",
                    "is_maint": False,
                    "resource_pool": "online",
                },
                {
                    "logic_compute_group_id": "g-hpc",
                    "gpu_count": 0,
                    "status": "READY",
                    "task_list": [],
                    "cordon_type": "",
                    "is_maint": False,
                    "resource_pool": "online",
                },
                {
                    "logic_compute_group_id": "g-cpu-2",
                    "gpu_count": 0,
                    "status": "READY",
                    "task_list": [],
                    "cordon_type": "",
                    "is_maint": False,
                    "resource_pool": "online",
                },
            ],
        ),
        patch.object(
            flow_module.browser_api_module,
            "get_resource_prices",
            side_effect=lambda workspace_id, logic_compute_group_id, session=None: [
                {"gpu_count": 0}
            ],
        ),
    ):
        group, _ = _match_cpu_only_compute_group(groups, workspace_id="ws-cpu", session=object())

    assert group is not None
    assert group["name"] == "HPC-可上网区资源-2"


def test_match_cpu_only_ignores_cpu_named_group_without_specs() -> None:
    groups = [
        {
            "logic_compute_group_id": "g-cpu",
            "name": "CPU资源",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [],
        },
        {
            "logic_compute_group_id": "g-cpu-2",
            "name": "CPU资源-2",
            "workspace_ids": ["ws-cpu"],
            "gpu_type_stats": [],
        },
    ]

    with (
        patch.object(
            flow_module.web_session_module,
            "fetch_workspace_availability",
            return_value=[
                {
                    "logic_compute_group_id": "g-cpu",
                    "gpu_count": 0,
                    "status": "READY",
                    "task_list": [],
                    "cordon_type": "",
                    "is_maint": False,
                    "resource_pool": "online",
                },
                {
                    "logic_compute_group_id": "g-cpu-2",
                    "gpu_count": 0,
                    "status": "READY",
                    "task_list": [],
                    "cordon_type": "",
                    "is_maint": False,
                    "resource_pool": "online",
                },
            ],
        ),
        patch.object(
            flow_module.browser_api_module,
            "get_resource_prices",
            side_effect=lambda workspace_id, logic_compute_group_id, session=None: (
                [] if logic_compute_group_id == "g-cpu" else [{"gpu_count": 0, "cpu_count": 4}]
            ),
        ),
    ):
        group, _ = _match_cpu_only_compute_group(groups, workspace_id="ws-cpu", session=object())

    assert group is not None
    assert group["name"] == "CPU资源-2"


def test_match_cpu_only_returns_none_when_no_workspace_bound_group_has_cpu_resources() -> None:
    groups = [
        {
            "logic_compute_group_id": "g-h200",
            "name": "GPU-H200",
            "workspace_ids": ["ws-other"],
            "gpu_type_stats": [{"gpu_info": {"gpu_type": "H200"}}],
        },
        {
            "logic_compute_group_id": "g-cpu",
            "name": "CPU资源",
            "gpu_type_stats": [],
        },
    ]

    group, _ = _match_cpu_only_compute_group(groups, workspace_id="ws-cpu", session=object())
    assert group is None
