"""TOML parsing and config file discovery for Inspire CLI config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib

import tomlkit

from inspire.config.models import CONFIG_FILENAME, PROJECT_CONFIG_DIR, ConfigError
from inspire.config.schema import get_option_by_toml
from inspire.config.schema_models import ConfigOption


def _find_project_config() -> Path | None:
    current = Path.cwd()
    while current != current.parent:
        config_path = current / PROJECT_CONFIG_DIR / CONFIG_FILENAME
        if config_path.exists():
            return config_path
        current = current.parent
    return None


def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _flatten_toml(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten_toml(value, full_key))
        else:
            result[full_key] = value
    return result


def _toml_key_to_field(toml_key: str) -> str | None:
    option = get_option_by_toml(toml_key)
    return option.field_name if option else None


def _validate_toml_value(option: ConfigOption, value: Any) -> Any:
    if option.value_type is not None and not isinstance(value, option.value_type):
        raise ConfigError(
            f"Invalid type for {option.toml_key}: expected {option.value_type.__name__}, "
            f"got {type(value).__name__}"
        )
    if option.parser and isinstance(value, str):
        try:
            return option.parser(value)
        except (ValueError, TypeError) as exc:
            raise ConfigError(f"Invalid value for {option.toml_key}: {exc}") from exc
    return value


def save_config(config) -> None:
    """Save workspace_specs to global config file.

    Only workspace_specs is persisted. Other config fields are loaded from
    environment/files and should not be written back.

    Args:
        config: Config object with workspace_specs to save

    Raises:
        ConfigError: If unable to write config file
    """
    from inspire.config.models import Config

    if not isinstance(config, Config):
        raise ConfigError("Expected Config instance")

    config_path = Config.resolve_global_config_path()

    # Load existing config or create new
    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    else:
        data = {}

    # Update workspace_specs section
    if config.workspace_specs:
        data["workspace_specs"] = config.workspace_specs
    elif "workspace_specs" in data:
        del data["workspace_specs"]

    # Write back
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            tomlkit.dump(data, f)
    except Exception as e:
        raise ConfigError(f"Failed to write config file {config_path}: {e}") from e
