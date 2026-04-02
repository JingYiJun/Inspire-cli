"""Tests for workspace_specs module with simplified functions."""

from unittest.mock import MagicMock, patch

import pytest

from inspire.config.models import Config
from inspire.platform.openapi.models import GPUType, ResourceSpec
from inspire.platform.openapi.workspace_specs import (
    fetch_workspace_specs,
    load_specs_from_config,
    save_specs_to_config,
)


class TestLoadSpecsFromConfig:
    """Test loading specs from config."""

    def test_load_existing_specs(self):
        """Should load and parse specs from config."""
        config = MagicMock(spec=Config)
        config.workspace_specs = {
            "ws-test": [
                {
                    "spec_id": "test-spec-1",
                    "gpu_type": "H200",
                    "gpu_count": 8,
                    "cpu_cores": 120,
                    "memory_gb": 1600,
                    "gpu_memory_gb": 141,
                    "description": "8x H200 test",
                }
            ]
        }

        specs = load_specs_from_config(config, "ws-test")

        assert len(specs) == 1
        assert specs[0].spec_id == "test-spec-1"
        assert specs[0].gpu_type == GPUType.H200
        assert specs[0].gpu_count == 8

    def test_load_missing_workspace(self):
        """Should return None for unknown workspace."""
        config = MagicMock(spec=Config)
        config.workspace_specs = {}

        specs = load_specs_from_config(config, "ws-unknown")

        assert specs is None

    def test_skip_invalid_specs(self):
        """Should skip invalid specs and return valid ones."""
        config = MagicMock(spec=Config)
        config.workspace_specs = {
            "ws-test": [
                {
                    "spec_id": "valid-spec",
                    "gpu_type": "H200",
                    "gpu_count": 8,
                    "cpu_cores": 120,
                    "memory_gb": 1600,
                },
                {
                    "spec_id": "invalid-spec",
                    "gpu_type": "INVALID_GPU",
                    "gpu_count": 8,
                    "cpu_cores": 120,
                    "memory_gb": 1600,
                },
                {"spec_id": "missing-fields"},
            ]
        }

        specs = load_specs_from_config(config, "ws-test")

        assert len(specs) == 1
        assert specs[0].spec_id == "valid-spec"


class TestSaveSpecsToConfig:
    """Test saving specs to config."""

    def test_save_specs(self):
        """Should save specs in flat structure."""
        config = MagicMock(spec=Config)
        config.workspace_specs = {}

        specs = [
            ResourceSpec(
                gpu_type=GPUType.H200,
                gpu_count=8,
                cpu_cores=120,
                memory_gb=1600,
                gpu_memory_gb=141,
                spec_id="spec-h200-8",
                description="8x H200",
            )
        ]

        # Note: save_config is imported inside the function, so we can't easily mock it
        # We just verify the data structure is correct - the actual save is tested elsewhere
        save_specs_to_config(config, "ws-test", specs)

        assert "ws-test" in config.workspace_specs
        saved = config.workspace_specs["ws-test"]
        assert len(saved) == 1
        assert saved[0]["spec_id"] == "spec-h200-8"
        assert saved[0]["gpu_type"] == "H200"


class TestFetchWorkspaceSpecs:
    """Test fetching specs from browser API."""

    @pytest.fixture
    def sample_api_response(self):
        """Sample API response data."""
        return {
            "gpu_count": 8,
            "quota_id": "test-spec-id",
            "cpu_count": 120,
            "memory_size_gib": 1600,
            "gpu_info": {
                "gpu_type": "H200",
                "gpu_memory": 141,
            },
            "name": "8x H200 Test",
        }

    def test_fetch_specs_success(self, sample_api_response):
        """Should fetch and parse specs from API."""
        mock_session = MagicMock()
        mock_workspace_id = "ws-test"

        # Patch at the module where the functions are imported FROM
        with patch(
            "inspire.platform.web.browser_api.notebooks._get_session_and_workspace_id"
        ) as mock_get:
            mock_get.return_value = (mock_session, mock_workspace_id)

            with patch("inspire.platform.web.browser_api.list_compute_groups") as mock_groups:
                mock_groups.return_value = [{"logic_compute_group_id": "cg-1"}]

                with patch(
                    "inspire.platform.web.browser_api.notebooks._request_notebooks_data"
                ) as mock_req:
                    mock_req.return_value = [sample_api_response]

                    specs = fetch_workspace_specs("ws-test")

        assert len(specs) == 1
        assert specs[0].spec_id == "test-spec-id"
        assert specs[0].gpu_type == GPUType.H200
        assert specs[0].gpu_count == 8

    def test_fetch_specs_empty_response(self):
        """Should return empty list when no specs found."""
        mock_session = MagicMock()

        with patch(
            "inspire.platform.web.browser_api.notebooks._get_session_and_workspace_id"
        ) as mock_get:
            mock_get.return_value = (mock_session, "ws-test")

            with patch("inspire.platform.web.browser_api.list_compute_groups") as mock_groups:
                mock_groups.return_value = []

                specs = fetch_workspace_specs("ws-test")

        assert specs == []

    def test_fetch_specs_api_error(self):
        """Should raise RuntimeError on API failure."""
        with patch(
            "inspire.platform.web.browser_api.notebooks._get_session_and_workspace_id"
        ) as mock_get:
            mock_get.side_effect = Exception("API Error")

            with pytest.raises(RuntimeError, match="Failed to probe"):
                fetch_workspace_specs("ws-test")
