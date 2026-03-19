"""Git forge abstraction for GitHub and Gitea Actions.

This module provides a unified interface for interacting with both
GitHub Actions and Gitea Actions APIs, which are largely compatible
but have some differences in authentication and endpoints.

The factory function create_forge_client() returns the appropriate
client based on the configured platform.
"""

from __future__ import annotations

from .artifacts import (
    _find_artifact_by_name,
    wait_for_log_artifact,
)
from .clients import (
    ForgeClient,
    GiteaClient,
    GitHubClient,
    create_forge_client,
)
from .config import (
    _get_active_repo,
    _get_active_server,
    _get_active_token,
    _get_active_workflow_file,
    _resolve_platform,
    _sanitize_token,
)
from .helpers import (
    _artifact_name,
    _extract_total_count,
    _find_run_by_inputs,
    _matches_inputs,
    _parse_event_inputs,
)
from .logs import (
    _prune_old_logs,
    fetch_remote_log_incremental,
    fetch_remote_log_via_bridge,
)
from .models import (
    ForgeAuthError,
    ForgeError,
    GitPlatform,
    GiteaAuthError,
    GiteaError,
)
from .workflows import (
    get_workflow_run,
    get_workflow_runs,
    trigger_log_retrieval_workflow,
    trigger_sync_workflow,
    trigger_workflow_dispatch,
    wait_for_workflow_completion,
)


__all__ = [
    # Models / errors
    "GitPlatform",
    "ForgeAuthError",
    "ForgeError",
    "GiteaAuthError",
    "GiteaError",
    # Config / platform resolution
    "_sanitize_token",
    "_resolve_platform",
    "_get_active_repo",
    "_get_active_token",
    "_get_active_server",
    "_get_active_workflow_file",
    # Clients
    "ForgeClient",
    "GiteaClient",
    "GitHubClient",
    "create_forge_client",
    # Helpers
    "_extract_total_count",
    "_parse_event_inputs",
    "_matches_inputs",
    "_find_run_by_inputs",
    "_artifact_name",
    "_find_artifact_by_name",
    "_prune_old_logs",
    # Workflows
    "trigger_workflow_dispatch",
    "trigger_log_retrieval_workflow",
    "trigger_sync_workflow",
    "get_workflow_runs",
    "get_workflow_run",
    "wait_for_workflow_completion",
    # Artifacts / logs
    "wait_for_log_artifact",
    "fetch_remote_log_via_bridge",
    "fetch_remote_log_incremental",
]
