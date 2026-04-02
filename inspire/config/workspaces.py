"""Workspace selection utilities.

The Inspire platform separates resources by workspace. For convenience, the CLI can
auto-select a workspace based on requested resources.
"""

from __future__ import annotations

import re
from typing import Optional

from inspire.config import Config, ConfigError

_WORKSPACE_ID_RE = re.compile(
    r"^ws-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_PLACEHOLDER_WORKSPACE_ID = "ws-00000000-0000-0000-0000-000000000000"


def _validate_workspace_id(value: str) -> None:
    if value == _PLACEHOLDER_WORKSPACE_ID:
        raise ConfigError(
            "workspace_id is set to the placeholder value. "
            "Configure a real workspace id in config.toml or set INSPIRE_WORKSPACE_ID."
        )
    if not _WORKSPACE_ID_RE.match(value):
        raise ConfigError(f"Invalid workspace_id format: {value!r}")


def _account_workspaces_hint(config: Config, alias: str) -> str:
    username = str(getattr(config, "username", "") or "").strip()
    if username:
        return f'Set [accounts."{username}".workspaces].{alias} in config.toml.'
    return f'Set [accounts."<username>".workspaces].{alias} in config.toml.'


def select_workspace_id(
    config: Config,
    *,
    gpu_type: Optional[str] = None,
    cpu_only: Optional[bool] = None,
    prefer_internet: bool = False,
    explicit_workspace_id: Optional[str] = None,
    explicit_workspace_name: Optional[str] = None,
    legacy_workspace_id: Optional[str] = None,
) -> Optional[str]:
    """Select a workspace_id based on requested resource type.

    Precedence:
      1) explicit_workspace_id
      2) explicit_workspace_name
      3) Routed workspaces.* entries (cpu/gpu/internet)
      4) Legacy raw workspace_id fallback

    Args:
        config: Loaded CLI config
        gpu_type: Requested GPU type (e.g. "H100", "H200", "4090")
        cpu_only: Whether the request is CPU-only
        prefer_internet: If True, prefer workspaces.internet when available
        explicit_workspace_id: Direct override
        explicit_workspace_name: Workspace alias/name (from account-scoped workspaces config)
        legacy_workspace_id: Deprecated raw workspace_id fallback for compatibility

    Returns:
        The selected workspace id, or None if not configured.
    """
    if explicit_workspace_id:
        _validate_workspace_id(explicit_workspace_id)
        return explicit_workspace_id

    if explicit_workspace_name:
        key = explicit_workspace_name.strip()
        if not key:
            raise ConfigError("Workspace name cannot be empty")

        normalized = key.lower()
        if normalized in {"cpu", "default"}:
            candidate = config.workspace_cpu_id
            if not candidate:
                raise ConfigError(
                    f"No CPU workspace configured. {_account_workspaces_hint(config, 'cpu')}"
                )
            _validate_workspace_id(candidate)
            return candidate

        if normalized == "gpu":
            candidate = config.workspace_gpu_id
            if not candidate:
                raise ConfigError(
                    f"No GPU workspace configured. {_account_workspaces_hint(config, 'gpu')}"
                )
            _validate_workspace_id(candidate)
            return candidate

        if normalized in {"internet", "net", "gpu_internet"}:
            candidate = config.workspace_internet_id or config.workspace_gpu_id
            if not candidate:
                raise ConfigError(
                    f"No internet workspace configured. {_account_workspaces_hint(config, 'internet')}"
                )
            _validate_workspace_id(candidate)
            return candidate

        if normalized in {"hpc", "cpu_hpc"}:
            candidate = (
                config.workspace_hpc_id or config.workspace_cpu_id or config.job_workspace_id
            )
            if not candidate:
                raise ConfigError(
                    "No HPC workspace configured. Set [workspaces].hpc or INSPIRE_WORKSPACE_HPC_ID."
                )
            _validate_workspace_id(candidate)
            return candidate

        candidate = None
        for name, workspace_id in (config.workspaces or {}).items():
            if name.lower() == normalized:
                candidate = workspace_id
                break

        if not candidate:
            available = sorted((config.workspaces or {}).keys())
            available_hint = ", ".join(available) if available else "(none configured)"
            raise ConfigError(
                f"Unknown workspace name: {explicit_workspace_name!r}. "
                "Configure it under "
                f'[accounts."<username>".workspaces] in config.toml. Available: {available_hint}'
            )

        _validate_workspace_id(candidate)
        return candidate

    # CPU requests (or commands without resource signal) default to workspaces.cpu.
    if cpu_only is True:
        candidate = config.workspace_cpu_id
        if candidate:
            _validate_workspace_id(candidate)
            return candidate
        if legacy_workspace_id:
            _validate_workspace_id(legacy_workspace_id)
            return legacy_workspace_id
        return candidate

    gpu_upper = (gpu_type or "").strip().upper()
    wants_internet = prefer_internet or ("4090" in gpu_upper)

    if gpu_type is not None:
        if wants_internet:
            candidate = config.workspace_internet_id or config.workspace_gpu_id
        else:
            candidate = config.workspace_gpu_id

        if candidate:
            _validate_workspace_id(candidate)
            return candidate
        if legacy_workspace_id:
            _validate_workspace_id(legacy_workspace_id)
            return legacy_workspace_id
        return None

    candidate = config.workspace_cpu_id
    if candidate:
        _validate_workspace_id(candidate)
        return candidate
    if legacy_workspace_id:
        _validate_workspace_id(legacy_workspace_id)
        return legacy_workspace_id
    return None
