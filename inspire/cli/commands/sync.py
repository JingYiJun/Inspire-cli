"""Sync local code to Bridge via rsync over the SSH tunnel."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Mapping, Optional

import click

from inspire.bridge.tunnel import (
    BridgeProfile,
    is_tunnel_available,
    load_tunnel_config,
    sync_via_rsync,
)
from inspire.cli.commands.init.toml_helpers import _toml_dumps
from inspire.cli.context import (
    Context,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
    pass_context,
)
from inspire.cli.utils.output import (
    emit_error as emit_output_error,
    emit_success as emit_output_success,
)
from inspire.config import CONFIG_FILENAME, PROJECT_CONFIG_DIR, SOURCE_ENV, Config, ConfigError
from inspire.config.toml import _load_toml

logger = logging.getLogger(__name__)


def _resolve_source_dir() -> Path:
    resolved = Path.cwd().resolve()
    if not resolved.exists():
        raise ConfigError(f"Sync source directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise ConfigError(f"Sync source path is not a directory: {resolved}")
    return resolved


def _resolve_config_value(
    config: Config,
    sources: Mapping[str, str],
    field_name: str,
    cli_value: Optional[str],
) -> str:
    if cli_value is not None:
        return str(cli_value).strip()

    if sources.get(field_name) == SOURCE_ENV:
        return ""

    return str(getattr(config, field_name, "") or "").strip()


def _resolve_target_dir(
    config: Config,
    sources: Mapping[str, str],
    target_dir: Optional[str],
) -> str:
    raw = _resolve_config_value(config, sources, "target_dir", target_dir)
    if not raw:
        raise ConfigError(
            "Missing sync target directory configuration.\n"
            "Set it once with:\n"
            "  inspire sync <bridge> /path/to/remote/dir\n"
            "Or add to config.toml:\n"
            "  [paths]\n"
            "  target_dir = '/path/to/remote/dir'"
        )
    return raw


def _resolve_sync_bridge_name(
    config: Config,
    sources: Mapping[str, str],
    bridge_name: Optional[str],
) -> str:
    raw = _resolve_config_value(config, sources, "sync_bridge", bridge_name)
    if not raw:
        raise ConfigError(
            "Missing sync bridge configuration.\n"
            "Set it once with:\n"
            "  inspire sync <bridge> /path/to/remote/dir\n"
            "Or add to config.toml:\n"
            "  [paths]\n"
            "  sync_bridge = 'bridge-name'"
        )
    return raw


def _project_config_write_path(config: Config) -> Path:
    project_path = getattr(config, "_project_config_path", None)
    if isinstance(project_path, Path):
        return project_path
    return Path.cwd() / PROJECT_CONFIG_DIR / CONFIG_FILENAME


def _persist_sync_settings(
    config: Config,
    *,
    target_dir: Optional[str] = None,
    sync_bridge: Optional[str] = None,
) -> Path:
    config_path = _project_config_write_path(config)
    existing_data: dict = {}
    if config_path.exists():
        try:
            existing_data = _load_toml(config_path)
        except Exception:
            existing_data = {}

    paths_section = existing_data.setdefault("paths", {})
    if not isinstance(paths_section, dict):
        paths_section = {}
        existing_data["paths"] = paths_section
    if target_dir is not None:
        paths_section["target_dir"] = str(target_dir)
    if sync_bridge is not None:
        paths_section["sync_bridge"] = str(sync_bridge)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_toml_dumps(existing_data), encoding="utf-8")
    return config_path


def _select_bridge(
    *,
    ctx: Context,
    bridge_name: str,
    retries: int,
    retry_pause: float,
) -> BridgeProfile:
    tunnel_config = load_tunnel_config()
    bridge = tunnel_config.get_bridge(bridge_name)
    if bridge is None:
        hint = "Use 'inspire tunnel list' to see available bridge profiles."
        emit_output_error(
            ctx,
            error_type="BridgeNotFound",
            message=f"Bridge '{bridge_name}' not found",
            exit_code=EXIT_CONFIG_ERROR,
            hint=hint,
            human_lines=[f"Error: Bridge '{bridge_name}' not found.", f"Hint: {hint}"],
        )
        sys.exit(EXIT_CONFIG_ERROR)

    if is_tunnel_available(
        bridge_name=bridge.name,
        config=tunnel_config,
        retries=retries,
        retry_pause=retry_pause,
    ):
        return bridge

    hint = "Run 'inspire tunnel status' or recreate a bridge profile."
    emit_output_error(
        ctx,
        error_type="TunnelUnavailable",
        message=f"SSH tunnel is not available for bridge '{bridge.name}'",
        exit_code=EXIT_GENERAL_ERROR,
        hint=hint,
        human_lines=[
            f"Error: SSH tunnel is not available for bridge '{bridge.name}'.",
            f"Hint: {hint}",
        ],
    )
    sys.exit(EXIT_GENERAL_ERROR)


@click.command()
@click.argument("bridge", required=False)
@click.argument("target_dir", required=False)
@click.option(
    "--timeout",
    default=300,
    show_default=True,
    help="Timeout in seconds for rsync",
)
@pass_context
def sync(
    ctx: Context,
    bridge: Optional[str],
    target_dir: Optional[str],
    timeout: int,
) -> None:
    """Sync a local directory to the Bridge shared filesystem via SSH rsync.

    The first run should provide both `bridge` and `target_dir`, for example:
    `inspire sync your-bridge /inspire/.../path/to/your/project`.
    They are then saved to `./.inspire/config.toml` and reused by later
    `inspire sync` runs. The local source directory is always the current
    working directory. Remote-only files are preserved by default.

    \b
    Examples:
        inspire sync your-bridge /inspire/.../path/to/your/project
        inspire sync

    \b
    `sync` only accepts `bridge` and `target_dir` from CLI arguments or
    saved config. It does not read `INSPIRE_SYNC_BRIDGE` or
    `INSPIRE_TARGET_DIR`.
    """
    try:
        config, sources = Config.from_files_and_env(
            require_target_dir=False,
            require_credentials=False,
        )
    except ConfigError as e:
        emit_output_error(
            ctx,
            error_type="ConfigError",
            message=str(e),
            exit_code=EXIT_CONFIG_ERROR,
            human_lines=[f"Configuration error: {e}"],
        )
        sys.exit(EXIT_CONFIG_ERROR)

    try:
        resolved_source_dir = _resolve_source_dir()
    except ConfigError as e:
        emit_output_error(
            ctx,
            error_type="ConfigError",
            message=str(e),
            exit_code=EXIT_CONFIG_ERROR,
            human_lines=[f"Configuration error: {e}"],
        )
        sys.exit(EXIT_CONFIG_ERROR)

    try:
        resolved_target_dir = _resolve_target_dir(config, sources, target_dir)
        requested_bridge_name = _resolve_sync_bridge_name(config, sources, bridge)
    except ConfigError as e:
        emit_output_error(
            ctx,
            error_type="ConfigError",
            message=str(e),
            exit_code=EXIT_CONFIG_ERROR,
            human_lines=[f"Configuration error: {e}"],
        )
        sys.exit(EXIT_CONFIG_ERROR)

    config_path = None
    selected_bridge = _select_bridge(
        ctx=ctx,
        bridge_name=requested_bridge_name,
        retries=config.tunnel_retries,
        retry_pause=config.tunnel_retry_pause,
    )

    if bridge is not None or target_dir is not None:
        try:
            config_path = _persist_sync_settings(
                config,
                target_dir=resolved_target_dir if target_dir is not None else None,
                sync_bridge=selected_bridge.name if bridge is not None else None,
            )
        except Exception as e:
            emit_output_error(
                ctx,
                error_type="ConfigError",
                message=f"Failed to persist sync settings: {e}",
                exit_code=EXIT_CONFIG_ERROR,
                human_lines=[f"Configuration error: Failed to persist sync settings: {e}"],
            )
            sys.exit(EXIT_CONFIG_ERROR)

    if ctx.debug and not ctx.json_output:
        click.echo(f"Using bridge: {selected_bridge.name}")
        click.echo(f"Local source: {resolved_source_dir}")
        click.echo(f"Remote target: {resolved_target_dir}")
        click.echo("Delete missing remote files: no")
        if config_path is not None:
            click.echo(f"Saved sync settings to: {config_path}")

    result = sync_via_rsync(
        source_dir=str(resolved_source_dir),
        target_dir=resolved_target_dir,
        bridge_name=selected_bridge.name,
        timeout=timeout,
        delete=False,
    )

    if result.get("success"):
        payload = {
            "status": "success",
            "method": "ssh_rsync",
            "bridge": selected_bridge.name,
            "source_dir": str(resolved_source_dir),
            "target_dir": resolved_target_dir,
            "delete": False,
        }
        if config_path is not None:
            payload["config_path"] = str(config_path)

        if ctx.debug and not ctx.json_output:
            click.echo(
                click.style("OK", fg="green")
                + f" Synced '{resolved_source_dir}' to {resolved_target_dir}"
            )
        else:
            emit_output_success(
                ctx,
                payload=payload,
                text=f"synced {resolved_source_dir} -> {resolved_target_dir}",
            )
        sys.exit(EXIT_SUCCESS)

    message = str(result.get("error") or "Unknown error")
    human_lines = [f"Sync failed: {message}"]
    emit_output_error(
        ctx,
        error_type="SyncError",
        message=message,
        exit_code=EXIT_GENERAL_ERROR,
        human_lines=human_lines,
    )
    sys.exit(EXIT_GENERAL_ERROR)


__all__ = ["sync"]
