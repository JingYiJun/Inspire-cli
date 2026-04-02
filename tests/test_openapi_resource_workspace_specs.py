"""Tests for workspace-aware specs with simplified implementation."""

import pytest
from unittest.mock import MagicMock

from inspire.platform.openapi.models import GPUType, ResourceSpec
from inspire.platform.openapi.resources import ResourceManager
from inspire.platform.openapi.workspace_specs import fetch_workspace_specs


class TestWorkspaceAwareSpecs:
    """Critical: specs are workspace-scoped, not global."""

    def test_resource_manager_has_no_specs_before_probe(self):
        """Specs should be empty until ensure_specs_for_workspace called."""
        mock_config = MagicMock()
        manager = ResourceManager(mock_config, skip_live_probe=True)

        with pytest.raises(RuntimeError, match="No workspace loaded"):
            _ = manager.resource_specs

    def test_resource_manager_probes_when_workspace_specified(self, monkeypatch):
        """Probe happens only when workspace is explicitly provided."""
        mock_config = MagicMock()
        mock_config.workspace_specs = {}

        manager = ResourceManager(mock_config, skip_live_probe=False)

        test_specs = [
            ResourceSpec(
                gpu_type=GPUType.H200,
                gpu_count=8,
                cpu_cores=120,
                memory_gb=1600,
                gpu_memory_gb=141,
                spec_id="ws-specific-spec",
                description="8x H200",
            )
        ]

        mock_fetch = MagicMock(return_value=test_specs)
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.fetch_workspace_specs",
            mock_fetch,
        )

        # Mock save_config to avoid Config instance check
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.save_specs_to_config",
            MagicMock(),
        )

        # Before probing - no specs
        with pytest.raises(RuntimeError):
            _ = manager.resource_specs

        # Probe for specific workspace
        manager.ensure_specs_for_workspace("ws-test")

        # After probing - specs available
        assert manager.resource_specs == test_specs
        mock_fetch.assert_called_once_with("ws-test")

    def test_resource_manager_caches_per_workspace(self, monkeypatch):
        """Same workspace = use cached specs. Different workspace = re-probe."""
        mock_config = MagicMock()
        mock_config.workspace_specs = {}

        manager = ResourceManager(mock_config, skip_live_probe=False)

        probe_call_count = 0

        def mock_fetch(workspace_id):
            nonlocal probe_call_count
            probe_call_count += 1
            return [
                ResourceSpec(
                    gpu_type=GPUType.H200,
                    gpu_count=8,
                    cpu_cores=120,
                    memory_gb=1600,
                    gpu_memory_gb=141,
                    spec_id=f"spec-{workspace_id}",
                    description="8x H200",
                )
            ]

        monkeypatch.setattr(
            "inspire.platform.openapi.resources.fetch_workspace_specs",
            mock_fetch,
        )

        # Mock save_config to avoid Config instance check
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.save_specs_to_config",
            MagicMock(),
        )

        # First call to ws-a should probe
        manager.ensure_specs_for_workspace("ws-a")
        assert probe_call_count == 1
        assert manager.resource_specs[0].spec_id == "spec-ws-a"

        # Second call to ws-a should use cache (no new probe)
        manager.ensure_specs_for_workspace("ws-a")
        assert probe_call_count == 1  # Still 1, not 2

        # Call to ws-b should probe again
        manager.ensure_specs_for_workspace("ws-b")
        assert probe_call_count == 2
        assert manager.resource_specs[0].spec_id == "spec-ws-b"

    def test_probe_failure_raises_clear_error(self, monkeypatch):
        """When probe fails, raise RuntimeError with workspace info."""
        mock_config = MagicMock()
        mock_config.workspace_specs = {}

        def mock_fetch(workspace_id):
            raise RuntimeError(
                "Failed to probe resource specs for workspace ws-special-id: Connection refused"
            )

        monkeypatch.setattr(
            "inspire.platform.openapi.resources.fetch_workspace_specs",
            mock_fetch,
        )

        manager = ResourceManager(mock_config, skip_live_probe=False)

        with pytest.raises(RuntimeError) as exc_info:
            manager.ensure_specs_for_workspace("ws-special-id")

        error_msg = str(exc_info.value)
        assert "ws-special-id" in error_msg
        assert "Failed to probe" in error_msg

    def test_specs_not_probed_at_resource_manager_init(self, monkeypatch):
        """__init__ should NOT trigger any probing."""
        mock_config = MagicMock()
        probe_called = False

        def mock_fetch(*args, **kwargs):
            nonlocal probe_called
            probe_called = True
            return []

        monkeypatch.setattr("inspire.platform.openapi.resources.fetch_workspace_specs", mock_fetch)

        # Create ResourceManager - should NOT probe
        _ = ResourceManager(mock_config, skip_live_probe=False)

        assert not probe_called, "ResourceManager.__init__ should not probe specs"

    def test_skip_live_probe_raises_before_attempting_probe(self, monkeypatch):
        """skip_live_probe=True should fail fast when live specs are requested."""
        mock_config = MagicMock()
        mock_fetch = MagicMock()
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.fetch_workspace_specs",
            mock_fetch,
        )

        manager = ResourceManager(mock_config, skip_live_probe=True)

        with pytest.raises(RuntimeError, match="disabled"):
            manager.ensure_specs_for_workspace("ws-test")

        mock_fetch.assert_not_called()


class TestBrowserProbeWorkspaceRouting:
    """The browser probe must use the requested workspace end-to-end."""

    def test_fetch_specs_passes_requested_workspace_through_all_browser_calls(self, monkeypatch):
        session_obj = object()
        request_bodies = []

        def fake_get_session_and_workspace_id(*, workspace_id, session=None):
            return session_obj, workspace_id

        def fake_list_compute_groups(*, workspace_id=None, session=None):
            assert workspace_id == "ws-target"
            return [{"logic_compute_group_id": "lcg-target"}]

        def fake_request_notebooks_data(
            session,
            method,
            endpoint_path,
            *,
            body=None,
            timeout=30,
            default_data=None,
        ):
            request_bodies.append(body)
            return [
                {
                    "quota_id": "spec-target",
                    "gpu_count": 8,
                    "cpu_count": 120,
                    "memory_size_gib": 1600,
                    "gpu_info": {"gpu_type": "H200", "gpu_memory": 141},
                    "name": "8x H200",
                }
            ]

        # Patch at the module where the functions are imported FROM
        monkeypatch.setattr(
            "inspire.platform.web.browser_api.notebooks._get_session_and_workspace_id",
            fake_get_session_and_workspace_id,
        )
        monkeypatch.setattr(
            "inspire.platform.web.browser_api.list_compute_groups",
            fake_list_compute_groups,
        )
        monkeypatch.setattr(
            "inspire.platform.web.browser_api.notebooks._request_notebooks_data",
            fake_request_notebooks_data,
        )

        specs = fetch_workspace_specs("ws-target")

        assert [spec.spec_id for spec in specs] == ["spec-target"]
        assert request_bodies == [
            {
                "workspace_id": "ws-target",
                "schedule_config_type": "SCHEDULE_CONFIG_TYPE_TRAIN",
                "logic_compute_group_id": "lcg-target",
            }
        ]

    def test_fetch_specs_uses_workspace_specific_compute_groups(self, monkeypatch):
        session_obj = object()

        def fake_get_session_and_workspace_id(*, workspace_id, session=None):
            return session_obj, workspace_id

        def fake_list_compute_groups(*, workspace_id=None, session=None):
            if workspace_id == "ws-a":
                return [{"logic_compute_group_id": "lcg-a"}]
            if workspace_id == "ws-b":
                return [{"logic_compute_group_id": "lcg-b"}]
            return []

        def fake_request_notebooks_data(
            session,
            method,
            endpoint_path,
            *,
            body=None,
            timeout=30,
            default_data=None,
        ):
            quota_id = "spec-a" if body["logic_compute_group_id"] == "lcg-a" else "spec-b"
            return [
                {
                    "quota_id": quota_id,
                    "gpu_count": 8,
                    "cpu_count": 120,
                    "memory_size_gib": 1600,
                    "gpu_info": {"gpu_type": "H200", "gpu_memory": 141},
                    "name": quota_id,
                }
            ]

        # Patch at the module where the functions are imported FROM
        # Patch at the module where the functions are imported FROM
        monkeypatch.setattr(
            "inspire.platform.web.browser_api.notebooks._get_session_and_workspace_id",
            fake_get_session_and_workspace_id,
        )
        monkeypatch.setattr(
            "inspire.platform.web.browser_api.list_compute_groups",
            fake_list_compute_groups,
        )
        monkeypatch.setattr(
            "inspire.platform.web.browser_api.notebooks._request_notebooks_data",
            fake_request_notebooks_data,
        )

        specs_a = fetch_workspace_specs("ws-a")
        specs_b = fetch_workspace_specs("ws-b")

        assert [spec.spec_id for spec in specs_a] == ["spec-a"]
        assert [spec.spec_id for spec in specs_b] == ["spec-b"]


class TestConfigCaching:
    """Specs should be cached in config."""

    def test_uses_cached_specs_when_available(self, monkeypatch):
        """Should use config cache instead of fetching."""
        mock_config = MagicMock()
        mock_config.workspace_specs = {
            "ws-test": [
                {
                    "spec_id": "cached-spec",
                    "gpu_type": "H200",
                    "gpu_count": 8,
                    "cpu_cores": 120,
                    "memory_gb": 1600,
                    "gpu_memory_gb": 141,
                    "description": "Cached spec",
                }
            ]
        }

        mock_fetch = MagicMock()
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.fetch_workspace_specs",
            mock_fetch,
        )

        manager = ResourceManager(mock_config, skip_live_probe=False)
        manager.ensure_specs_for_workspace("ws-test")

        # Should use cache, not fetch
        mock_fetch.assert_not_called()
        assert manager.resource_specs[0].spec_id == "cached-spec"

    def test_fetches_and_caches_when_not_in_config(self, monkeypatch):
        """Should fetch and save to config when not cached."""
        mock_config = MagicMock()
        mock_config.workspace_specs = {}

        test_specs = [
            ResourceSpec(
                gpu_type=GPUType.H200,
                gpu_count=8,
                cpu_cores=120,
                memory_gb=1600,
                gpu_memory_gb=141,
                spec_id="fetched-spec",
                description="Fetched spec",
            )
        ]

        mock_fetch = MagicMock(return_value=test_specs)
        mock_save = MagicMock()
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.fetch_workspace_specs",
            mock_fetch,
        )
        monkeypatch.setattr(
            "inspire.platform.openapi.resources.save_specs_to_config",
            mock_save,
        )

        manager = ResourceManager(mock_config, skip_live_probe=False)
        manager.ensure_specs_for_workspace("ws-test")

        mock_fetch.assert_called_once_with("ws-test")
        mock_save.assert_called_once()
        assert manager.resource_specs[0].spec_id == "fetched-spec"


class TestAuthManagerWorkspaceCache:
    """Cache must invalidate when workspace changes."""

    def test_cache_includes_workspace_in_key(self, monkeypatch):
        """Cache key should include workspace identifier."""
        import importlib

        auth_module = importlib.import_module("inspire.cli.utils.auth")

        # Create config with specific workspace
        config = MagicMock()
        config.base_url = "https://api.example.com"
        config.username = "testuser"
        config.password = "testpass"
        config.job_workspace_id = "ws-test-123"
        config.workspace_gpu_id = None
        config.workspace_cpu_id = None
        config.default_workspace_id = None
        config.timeout = 30
        config.max_retries = 3
        config.retry_delay = 1.0
        config.skip_ssl_verify = False
        config.force_proxy = False
        config.openapi_prefix = None
        config.auth_endpoint = None
        config.docker_registry = None
        config.compute_groups = []

        cache_key = auth_module.AuthManager._make_cache_key(config)

        # Cache key should include workspace
        assert "ws-test-123" in cache_key

    def test_different_workspace_creates_new_api(self, monkeypatch):
        """ws-a then ws-b = 2 API instances created."""
        import importlib

        auth_module = importlib.import_module("inspire.cli.utils.auth")

        created_configs = []

        class MockAPI:
            def __init__(self, config):
                created_configs.append(config)
                self.token = "token"

            def authenticate(self, u, p):
                pass

        monkeypatch.setattr(auth_module, "InspireAPI", MockAPI)
        auth_module.AuthManager.clear_cache()

        # Create configs with different workspaces
        config_a = MagicMock()
        config_a.base_url = "https://api.example.com"
        config_a.username = "user"
        config_a.password = "pass"
        config_a.job_workspace_id = "ws-a"
        config_a.workspace_gpu_id = None
        config_a.workspace_cpu_id = None
        config_a.default_workspace_id = None
        config_a.timeout = 30
        config_a.max_retries = 3
        config_a.retry_delay = 1.0
        config_a.skip_ssl_verify = False
        config_a.force_proxy = False
        config_a.openapi_prefix = None
        config_a.auth_endpoint = None
        config_a.docker_registry = None
        config_a.compute_groups = []

        config_b = MagicMock()
        config_b.base_url = "https://api.example.com"
        config_b.username = "user"
        config_b.password = "pass"
        config_b.job_workspace_id = "ws-b"
        config_b.workspace_gpu_id = None
        config_b.workspace_cpu_id = None
        config_b.default_workspace_id = None
        config_b.timeout = 30
        config_b.max_retries = 3
        config_b.retry_delay = 1.0
        config_b.skip_ssl_verify = False
        config_b.force_proxy = False
        config_b.openapi_prefix = None
        config_b.auth_endpoint = None
        config_b.docker_registry = None
        config_b.compute_groups = []

        # Get API for workspace A
        _ = auth_module.AuthManager.get_api(config_a)
        # Get API for workspace B - should create NEW API
        _ = auth_module.AuthManager.get_api(config_b)

        # Both APIs created (cache invalidated due to workspace change)
        assert len(created_configs) == 2

        auth_module.AuthManager.clear_cache()

    def test_same_workspace_reuses_cached_api(self, monkeypatch):
        """ws-a then ws-a = 1 API instance (cached)."""
        import importlib

        auth_module = importlib.import_module("inspire.cli.utils.auth")

        created_count = 0

        class MockAPI:
            def __init__(self, config):
                nonlocal created_count
                created_count += 1
                self.token = "token"

            def authenticate(self, u, p):
                pass

        monkeypatch.setattr(auth_module, "InspireAPI", MockAPI)
        auth_module.AuthManager.clear_cache()

        # Create config
        config = MagicMock()
        config.base_url = "https://api.example.com"
        config.username = "user"
        config.password = "pass"
        config.job_workspace_id = "ws-same"
        config.workspace_gpu_id = None
        config.workspace_cpu_id = None
        config.default_workspace_id = None
        config.timeout = 30
        config.max_retries = 3
        config.retry_delay = 1.0
        config.skip_ssl_verify = False
        config.force_proxy = False
        config.openapi_prefix = None
        config.auth_endpoint = None
        config.docker_registry = None
        config.compute_groups = []

        # Get API twice for same workspace
        api_a = auth_module.AuthManager.get_api(config)
        api_b = auth_module.AuthManager.get_api(config)

        # Should reuse cached API
        assert created_count == 1
        assert api_a is api_b

        auth_module.AuthManager.clear_cache()
