"""Environment-based config loading for Inspire CLI."""

from __future__ import annotations

import os

from inspire.config.env import _parse_denylist
from inspire.config.models import Config, ConfigError


def _parse_ssh_port(value: str | None) -> int:
    """Parse SSH port from environment variable.

    Args:
        value: The environment variable value (e.g., "22222")

    Returns:
        The parsed port number, or 22222 if not set/invalid
    """
    if not value:
        return 22222
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    return 22222


def config_from_env(*, require_target_dir: bool = False) -> Config:
    """Create configuration from environment variables."""
    username = os.getenv("INSPIRE_USERNAME")
    password = os.getenv("INSPIRE_PASSWORD")

    if not username:
        raise ConfigError(
            "Missing INSPIRE_USERNAME environment variable.\n"
            "Set it with: export INSPIRE_USERNAME='your_username'"
        )

    if not password:
        raise ConfigError(
            "Missing INSPIRE_PASSWORD environment variable.\n"
            "Set it with: export INSPIRE_PASSWORD='your_password'"
        )

    target_dir = os.getenv("INSPIRE_TARGET_DIR")

    if require_target_dir and not target_dir:
        raise ConfigError(
            "Missing INSPIRE_TARGET_DIR environment variable.\n"
            "This is required for Bridge operations (sync, exec, logs).\n"
            "Set it with: export INSPIRE_TARGET_DIR='/path/to/shared/directory'"
        )

    timeout = 30
    max_retries = 3
    retry_delay = 1.0

    timeout_env = os.getenv("INSPIRE_TIMEOUT")
    if timeout_env:
        try:
            timeout = int(timeout_env)
        except ValueError as e:
            raise ConfigError(
                "Invalid INSPIRE_TIMEOUT value. It must be an integer number of seconds."
            ) from e

    max_retries_env = os.getenv("INSPIRE_MAX_RETRIES")
    if max_retries_env:
        try:
            max_retries = int(max_retries_env)
        except ValueError as e:
            raise ConfigError("Invalid INSPIRE_MAX_RETRIES value. It must be an integer.") from e

    retry_delay_env = os.getenv("INSPIRE_RETRY_DELAY")
    if retry_delay_env:
        try:
            retry_delay = float(retry_delay_env)
        except ValueError as e:
            raise ConfigError(
                "Invalid INSPIRE_RETRY_DELAY value. It must be a number of seconds."
            ) from e

    bridge_action_timeout = 600
    bat_env = os.getenv("INSPIRE_BRIDGE_ACTION_TIMEOUT")
    if bat_env:
        try:
            bridge_action_timeout = int(bat_env)
        except ValueError as e:
            raise ConfigError(
                "Invalid INSPIRE_BRIDGE_ACTION_TIMEOUT value. It must be an integer number of seconds."
            ) from e

    return Config(
        username=username,
        password=password,
        base_url=os.getenv("INSPIRE_BASE_URL", "https://api.example.com"),
        target_dir=target_dir,
        log_pattern=os.getenv("INSPIRE_LOG_PATTERN", "training_master_*.log"),
        job_cache_path=os.getenv("INSPIRE_JOB_CACHE", "~/.inspire/jobs.json"),
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay,
        log_cache_dir=os.getenv("INSP_LOG_CACHE_DIR")
        or os.getenv("INSPIRE_LOG_CACHE_DIR", "~/.inspire/logs"),
        default_remote=os.getenv("INSPIRE_DEFAULT_REMOTE", "origin"),
        bridge_action_timeout=bridge_action_timeout,
        bridge_action_denylist=_parse_denylist(os.getenv("INSPIRE_BRIDGE_DENYLIST")),
        ssh_port=_parse_ssh_port(os.getenv("INSPIRE_SSH_PORT")),
    )
