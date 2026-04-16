"""Configuration models and shared constants for Inspire CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Config file paths
CONFIG_FILENAME = "config.toml"
PROJECT_CONFIG_DIR = ".inspire"  # ./.inspire/config.toml


class ConfigError(Exception):
    """Configuration error - missing or invalid settings."""


class ConfigDeprecationWarning(UserWarning):
    """Configuration deprecation warning."""


# Source tracking for config values
SOURCE_DEFAULT = "default"
SOURCE_GLOBAL = "global"
SOURCE_PROJECT = "project"
SOURCE_ENV = "env"
SOURCE_INFERRED = "inferred"


def _default_rtunnel_download_url() -> str:
    from inspire.config.rtunnel_defaults import DEFAULT_RTUNNEL_DOWNLOAD_URL

    return DEFAULT_RTUNNEL_DOWNLOAD_URL


@dataclass
class Config:
    """Inspire CLI configuration."""

    # Required (for platform API)
    username: str
    password: str

    # Optional with defaults
    base_url: str = "https://api.example.com"
    target_dir: Optional[str] = None  # INSPIRE_TARGET_DIR - unified for bridge/job operations
    sync_source_dir: Optional[str] = None  # Deprecated: sync always uses current directory
    sync_bridge: Optional[str] = (
        None  # Saved bridge for rsync sync; sync ignores env-sourced values
    )
    log_pattern: str = "training_master_*.log"
    job_cache_path: str = "~/.inspire/jobs.json"

    # API settings
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0

    log_cache_dir: str = "~/.inspire/logs"

    # Sync settings
    default_remote: str = "origin"

    # Bridge exec settings
    bridge_action_timeout: int = 600
    bridge_action_denylist: list[str] = field(default_factory=list)

    # API settings (additional)
    skip_ssl_verify: bool = False
    force_proxy: bool = False

    # API path prefixes (None = use code defaults)
    openapi_prefix: Optional[str] = None
    browser_api_prefix: Optional[str] = None
    auth_endpoint: Optional[str] = None
    docker_registry: Optional[str] = None

    # Job settings
    job_resource: Optional[str] = None
    job_priority: Optional[int] = None
    job_image: Optional[str] = None
    job_project_id: Optional[str] = None
    job_workspace_id: Optional[str] = None
    job_shm_size: Optional[int] = None

    # Shared command defaults ([defaults])
    default_resource: Optional[str] = None
    default_image: Optional[str] = None
    default_priority: Optional[int] = None
    default_workspace_id: Optional[str] = None

    # Workspace routing (optional)
    workspace_cpu_id: Optional[str] = None
    workspace_gpu_id: Optional[str] = None
    workspace_internet_id: Optional[str] = None
    workspace_hpc_id: Optional[str] = None

    # Full workspace map loaded from account-scoped [accounts."<user>".workspaces]
    workspaces: dict[str, str] = field(default_factory=dict)

    # Project alias map for project_id resolution (alias -> project-...)
    projects: dict[str, str] = field(default_factory=dict)

    # Discovered per-account project metadata (loaded from layered [accounts."<user>"] catalog)
    # project_id -> metadata dict (best-effort, schema may evolve)
    project_catalog: dict[str, dict[str, Any]] = field(default_factory=dict)
    # project_id -> shared-path grouping key (e.g. "/train/global_user/<user>")
    project_shared_path_groups: dict[str, str] = field(default_factory=dict)
    # project_id -> discovered workdir (best-effort; may come from API or probing)
    project_workdirs: dict[str, str] = field(default_factory=dict)
    # Account-level shared-path grouping key (if available)
    account_shared_path_group: Optional[str] = None
    # Account-level train job workdir (if available)
    account_train_job_workdir: Optional[str] = None

    # Legacy project context account binding (from deprecated [context].account)
    context_account: Optional[str] = None

    # Notebook settings
    notebook_resource: Optional[str] = None
    notebook_image: Optional[str] = None
    notebook_project_id: Optional[str] = None
    notebook_priority: Optional[int] = None
    notebook_workspace_id: Optional[str] = None
    notebook_shm_size: Optional[int] = None
    notebook_post_start: Optional[str] = None

    # HPC settings
    hpc_image: Optional[str] = None
    hpc_image_type: str = "SOURCE_PUBLIC"
    hpc_priority: int = 4
    hpc_ttl_after_finish_seconds: int = 600
    hpc_default_preset: Optional[str] = None
    hpc_presets: dict[str, dict[str, Any]] = field(default_factory=dict)

    # SSH settings (rtunnel_bin accepts a list in TOML; normalized to colon-separated str)
    rtunnel_bin: Optional[str] = None
    sshd_deb_dir: Optional[str] = None
    dropbear_deb_dir: Optional[str] = None
    setup_script: Optional[str] = None
    rtunnel_download_url: str = field(default_factory=_default_rtunnel_download_url)
    apt_mirror_url: Optional[str] = None
    rtunnel_upload_policy: str = "auto"
    ssh_port: int = 22222

    # Tunnel retry settings
    tunnel_retries: int = 3
    tunnel_retry_pause: float = 2.0

    # Other
    shm_size: Optional[int] = None

    # User-defined project selection order (list of project names or IDs)
    project_order: list[str] = field(default_factory=list)

    # Compute groups (loaded from config.toml [[compute_groups]] sections)
    compute_groups: list[dict] = field(default_factory=list)

    # Workspace resource specs registry (auto-discovered from browser API)
    # Structure: {workspace_id: [spec_dict, ...]}
    # Each spec_dict: {spec_id, gpu_type, gpu_count, cpu_cores, memory_gb, gpu_memory_gb, description}
    # Discovered once per workspace and persisted. No TTL - specs are stable.
    workspace_specs: dict[str, list[dict]] = field(default_factory=dict)

    # Workspace names registry (auto-discovered from browser API)
    # Structure: {workspace_id: "workspace_name"}
    # Maps workspace IDs to their human-readable names.
    workspace_names: dict[str, str] = field(default_factory=dict)

    # Remote environment variables (injected into bridge exec, jobs, run commands)
    remote_env: dict[str, str] = field(default_factory=dict)

    # Global per-account secrets map, loaded from global config:
    # [accounts."<username>"].password
    accounts: dict[str, str] = field(default_factory=dict)

    # Source precedence: "env" (default) = env vars win, "toml" = project TOML wins
    prefer_source: str = "env"

    def __post_init__(self) -> None:
        # Normalize list-valued fields to colon-separated strings so the rest
        # of the codebase can treat them uniformly as ``str``.
        if isinstance(self.rtunnel_bin, list):
            joined = ":".join(str(p).strip() for p in self.rtunnel_bin if str(p).strip())
            object.__setattr__(self, "rtunnel_bin", joined or None)

    # Class-level config paths
    GLOBAL_CONFIG_PATH_ENV_VAR = "INSPIRE_GLOBAL_CONFIG_PATH"
    GLOBAL_CONFIG_PATH = Path.home() / ".config" / "inspire" / CONFIG_FILENAME

    @classmethod
    def resolve_global_config_path(cls) -> Path:
        """Return the effective global config path, honoring env override."""
        override = str(os.getenv(cls.GLOBAL_CONFIG_PATH_ENV_VAR) or "").strip()
        if override:
            return Path(override).expanduser()
        return cls.GLOBAL_CONFIG_PATH

    def get_expanded_cache_path(self) -> str:
        """Get the job cache path with ~ expanded."""
        return os.path.expanduser(self.job_cache_path)

    @classmethod
    def _find_project_config(cls) -> Path | None:
        from inspire.config.toml import _find_project_config

        return _find_project_config()

    @staticmethod
    def _load_toml(path: Path) -> dict[str, Any]:
        from inspire.config.toml import _load_toml

        return _load_toml(path)

    @staticmethod
    def _flatten_toml(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        from inspire.config.toml import _flatten_toml

        return _flatten_toml(data, prefix)

    @classmethod
    def _toml_key_to_field(cls, toml_key: str) -> str | None:
        from inspire.config.toml import _toml_key_to_field

        return _toml_key_to_field(toml_key)

    @classmethod
    def from_env(cls, require_target_dir: bool = False) -> "Config":
        from inspire.config.load_env import config_from_env

        return config_from_env(require_target_dir=require_target_dir)

    @classmethod
    def from_files_and_env(
        cls, require_target_dir: bool = False, require_credentials: bool = True
    ) -> tuple["Config", dict[str, str]]:
        from inspire.config.load import config_from_files_and_env

        return config_from_files_and_env(
            require_target_dir=require_target_dir, require_credentials=require_credentials
        )

    @classmethod
    def get_config_paths(cls) -> tuple[Path | None, Path | None]:
        from inspire.config.load import get_config_paths

        return get_config_paths()
