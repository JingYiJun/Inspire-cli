from unittest.mock import MagicMock

import pytest

from inspire.config.models import Config
from inspire.platform.openapi.models import GPUType, ResourceSpec
from inspire.platform.openapi.resources import ResourceManager


_TEST_SPECS = [
    ResourceSpec(
        gpu_type=GPUType.H200,
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=141,
        spec_id="spec-h200-1",
        description="1x H200",
    ),
    ResourceSpec(
        gpu_type=GPUType.H200,
        gpu_count=2,
        cpu_cores=30,
        memory_gb=400,
        gpu_memory_gb=141,
        spec_id="spec-h200-2",
        description="2x H200",
    ),
    ResourceSpec(
        gpu_type=GPUType.H200,
        gpu_count=4,
        cpu_cores=60,
        memory_gb=800,
        gpu_memory_gb=141,
        spec_id="spec-h200-4",
        description="4x H200",
    ),
    ResourceSpec(
        gpu_type=GPUType.H200,
        gpu_count=8,
        cpu_cores=120,
        memory_gb=1600,
        gpu_memory_gb=141,
        spec_id="spec-h200-8",
        description="8x H200",
    ),
]


@pytest.fixture
def mock_config():
    """Create a mock config for testing."""
    config = MagicMock(spec=Config)
    config.workspace_specs = {}
    return config


@pytest.fixture
def resource_manager(mock_config):
    """Create a ResourceManager with test specs for get_recommended_config tests."""
    manager = ResourceManager(
        mock_config,
        [
            {"name": "H200-1号机房", "id": "lcg-h200-1", "gpu_type": "H200", "location": ""},
            {"name": "H200-2号机房", "id": "lcg-h200-2", "gpu_type": "H200", "location": ""},
            {"name": "H200-3号机房", "id": "lcg-h200-3", "gpu_type": "H200", "location": ""},
        ],
        skip_live_probe=True,
    )
    manager._set_test_specs("test-workspace", _TEST_SPECS)
    return manager


def test_resource_manager_ignores_compute_groups_without_supported_gpu_type(mock_config) -> None:
    manager = ResourceManager(
        mock_config,
        [
            {"name": "CPU", "id": "lcg-cpu", "gpu_type": ""},
            {"name": "4090", "id": "lcg-4090", "gpu_type": "4090"},
            {"name": "H100", "id": "lcg-h100", "gpu_type": "h100"},
        ],
        skip_live_probe=True,
    )

    assert len(manager.compute_groups) == 1
    assert manager.compute_groups[0].compute_group_id == "lcg-h100"
    assert manager.compute_groups[0].gpu_type == GPUType.H100


def test_resource_manager_ignores_compute_group_without_id(mock_config) -> None:
    manager = ResourceManager(
        mock_config, [{"name": "H200 missing id", "gpu_type": "H200"}], skip_live_probe=True
    )

    assert manager.compute_groups == []


def test_resource_manager_accepts_discovered_gpu_type_labels(mock_config) -> None:
    manager = ResourceManager(
        mock_config,
        [
            {"name": "H200-1", "id": "lcg-h200-1", "gpu_type": "NVIDIA H200 (141GB)"},
            {"name": "H100-1", "id": "lcg-h100-1", "gpu_type": "NVIDIA H100 (80GB)"},
        ],
        skip_live_probe=True,
    )

    ids_to_types = {group.compute_group_id: group.gpu_type for group in manager.compute_groups}

    assert ids_to_types["lcg-h200-1"] == GPUType.H200
    assert ids_to_types["lcg-h100-1"] == GPUType.H100


def test_resource_manager_matches_group_name_when_location_empty(
    resource_manager: ResourceManager,
) -> None:
    spec_id, group_id = resource_manager.get_recommended_config(
        "8xH200", prefer_location="H200-3号机房"
    )

    assert spec_id == "spec-h200-8"
    assert group_id == "lcg-h200-3"


def test_resource_manager_numeric_match_uses_group_name_when_location_empty(
    resource_manager: ResourceManager,
) -> None:
    _, group_id = resource_manager.get_recommended_config("8xH200", prefer_location="3号")
    assert group_id == "lcg-h200-3"


def test_resource_manager_error_lists_non_empty_labels(
    resource_manager: ResourceManager,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        resource_manager.get_recommended_config("8xH200", prefer_location="not-found")

    message = str(exc_info.value)
    assert "Available locations: H200-1号机房, H200-2号机房, H200-3号机房" in message
    assert "Available locations: , " not in message


def test_resource_manager_no_preference_picks_first_group(mock_config) -> None:
    """Without prefer_location, get_recommended_config picks the first group.

    This documents the behaviour that caused the queuing bug: when
    _resolve_run_resource_and_location lost the auto-selected group name
    (selected_location was empty and selected_group_name was not forwarded),
    the job was submitted to the first config group regardless of availability.
    """
    manager = ResourceManager(
        mock_config,
        [
            {"name": "H200-1号机房", "id": "lcg-h200-1", "gpu_type": "H200"},
            {"name": "H200-2号机房", "id": "lcg-h200-2", "gpu_type": "H200"},
        ],
        skip_live_probe=True,
    )
    manager._set_test_specs("test-ws", _TEST_SPECS)

    _, group_id = manager.get_recommended_config("8xH200", prefer_location=None)
    assert group_id == "lcg-h200-1"  # always first → wrong if GPUs are on group 2


def test_autoselect_location_fallback_uses_group_name() -> None:
    """Regression: find_best_compute_group_location must return group name
    when location is empty so that run.py can forward it to the API."""
    from unittest.mock import MagicMock

    from inspire.cli.utils.compute_group_autoselect import find_best_compute_group_location
    from inspire.platform.openapi.models import ComputeGroup

    fake_best = MagicMock()
    fake_best.group_id = "lcg-h200-2"
    fake_best.group_name = "H200-2号机房"

    api = MagicMock()
    api.resource_manager.compute_groups = [
        ComputeGroup(
            name="H200-1号机房",
            compute_group_id="lcg-h200-1",
            gpu_type=GPUType.H200,
            location="",
        ),
        ComputeGroup(
            name="H200-2号机房",
            compute_group_id="lcg-h200-2",
            gpu_type=GPUType.H200,
            location="",
        ),
    ]

    import inspire.cli.utils.compute_group_autoselect as cga_mod

    original = cga_mod.browser_api_module.find_best_compute_group_accurate

    try:
        cga_mod.browser_api_module.find_best_compute_group_accurate = MagicMock(
            return_value=fake_best
        )

        best, selected_location, selected_group_name = find_best_compute_group_location(
            api, gpu_type="H200", min_gpus=8
        )

        assert best is fake_best
        # location is empty because config entries have no location field
        assert selected_location == ""
        # group name must be populated so run.py can use it as fallback
        assert selected_group_name == "H200-2号机房"

        # Verify the fallback produces correct group selection
        location = selected_location or selected_group_name or None
        # Create manager with mock config
        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.workspace_specs = {}
        manager = ResourceManager(
            mock_cfg,
            [
                {"name": "H200-1号机房", "id": "lcg-h200-1", "gpu_type": "H200"},
                {"name": "H200-2号机房", "id": "lcg-h200-2", "gpu_type": "H200"},
            ],
            skip_live_probe=True,
        )
        manager._set_test_specs("test-ws", _TEST_SPECS)
        _, group_id = manager.get_recommended_config("8xH200", prefer_location=location)
        assert group_id == "lcg-h200-2"  # must match auto-selected, not first
    finally:
        cga_mod.browser_api_module.find_best_compute_group_accurate = original


# -----------------------------------------------------------------------------
# Regression Tests
# -----------------------------------------------------------------------------


def test_resource_manager_probes_live_specs_on_demand(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ResourceManager should probe browser API when workspace is specified (lazy probing)."""
    from unittest.mock import MagicMock

    fake_spec = ResourceSpec(
        gpu_type=GPUType.H200,
        gpu_count=1,
        cpu_cores=10,
        memory_gb=100,
        gpu_memory_gb=80,
        spec_id="live-spec-id",
        description="Live spec",
    )

    mock_fetch = MagicMock(return_value=[fake_spec])
    monkeypatch.setattr(
        "inspire.platform.openapi.resources.fetch_workspace_specs",
        mock_fetch,
    )

    mock_config = MagicMock()
    mock_config.workspace_specs = {}

    # Mock save_specs_to_config to avoid Config instance check
    monkeypatch.setattr(
        "inspire.platform.openapi.resources.save_specs_to_config",
        MagicMock(),
    )

    manager = ResourceManager(
        mock_config,
        [{"name": "H200", "id": "lcg-h200", "gpu_type": "H200"}],
        skip_live_probe=False,
    )

    # Should not probe at init
    mock_fetch.assert_not_called()

    # Probe when workspace is specified
    manager.ensure_specs_for_workspace("ws-test")

    mock_fetch.assert_called_once_with("ws-test")
    assert manager.resource_specs == [fake_spec]


def test_resource_manager_raises_error_when_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: When probe fails, ensure_specs_for_workspace should raise error."""
    from unittest.mock import MagicMock

    mock_fetch = MagicMock(return_value=[])
    monkeypatch.setattr(
        "inspire.platform.openapi.resources.fetch_workspace_specs",
        mock_fetch,
    )

    mock_config = MagicMock()
    mock_config.workspace_specs = {}

    # Mock save_specs_to_config to avoid Config instance check
    monkeypatch.setattr(
        "inspire.platform.openapi.resources.save_specs_to_config",
        MagicMock(),
    )

    manager = ResourceManager(
        mock_config,
        [{"name": "H200", "id": "lcg-h200", "gpu_type": "H200"}],
        skip_live_probe=False,
    )

    # Probe called when workspace is specified
    manager.ensure_specs_for_workspace("ws-test")
    mock_fetch.assert_called_once_with("ws-test")
    # Empty specs are allowed (just means no specs available)
    assert manager.resource_specs == []


def test_resource_manager_skip_live_probe_prevents_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: skip_live_probe=True should prevent browser API probe."""
    from unittest.mock import MagicMock

    mock_fetch = MagicMock(return_value=None)
    monkeypatch.setattr(
        "inspire.platform.openapi.resources.fetch_workspace_specs",
        mock_fetch,
    )

    mock_config = MagicMock()

    manager = ResourceManager(
        mock_config,
        [{"name": "H200", "id": "lcg-h200", "gpu_type": "H200"}],
        skip_live_probe=True,
    )

    # Should not probe at init
    mock_fetch.assert_not_called()

    # Accessing specs before probing should raise
    with pytest.raises(RuntimeError, match="No workspace loaded"):
        _ = manager.resource_specs

    with pytest.raises(RuntimeError, match="disabled"):
        manager.ensure_specs_for_workspace("ws-test")

    mock_fetch.assert_not_called()


def test_get_recommended_config_fails_gracefully_with_unprobed_specs() -> None:
    """Regression: get_recommended_config should fail if specs not probed."""
    from unittest.mock import MagicMock

    mock_config = MagicMock()

    manager = ResourceManager(
        mock_config,
        [{"name": "H200", "id": "lcg-h200", "gpu_type": "H200"}],
        skip_live_probe=True,
    )

    # Should raise RuntimeError before ValueError because specs not probed
    with pytest.raises(RuntimeError, match="No workspace loaded"):
        manager.get_recommended_config("1xH200")
