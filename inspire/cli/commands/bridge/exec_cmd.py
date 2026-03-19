"""Bridge exec command -- execute a shell command on the Bridge runner."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Callable, Optional

import click

from inspire.cli.context import (
    Context,
    EXIT_GENERAL_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    pass_context,
)
from inspire.config import Config, ConfigError, build_env_exports
from inspire.bridge.tunnel import (
    BridgeProfile,
    TunnelNotAvailableError,
    is_tunnel_available,
    run_ssh_command,
    run_ssh_command_streaming,
    load_tunnel_config,
)
from inspire.cli.utils.errors import emit_error as _emit_error
from inspire.cli.utils.notebook_cli import require_web_session
from inspire.cli.utils.output import (
    emit_error as emit_output_error,
    emit_success as emit_output_success,
)
from inspire.cli.utils.tunnel_reconnect import (
    NotebookBridgeReconnectState,
    NotebookBridgeReconnectStatus,
    attempt_notebook_bridge_rebuild,
    load_ssh_public_key_material,
    rebuild_notebook_bridge_profile,
    should_attempt_ssh_reconnect,
)
from inspire.config.ssh_runtime import resolve_ssh_runtime_config
from inspire.platform.web import browser_api as browser_api_module

logger = logging.getLogger(__name__)
_TERMINAL_NOTEBOOK_STATUSES = frozenset({"FAILED", "ERROR", "STOPPED", "DELETED"})


def _build_remote_command(*, command: str, target_dir: str, remote_env: dict[str, str]) -> str:
    env_exports = build_env_exports(remote_env)
    return f'{env_exports}cd "{target_dir}" && {command}'


def _verbose_output(ctx: Context) -> bool:
    return not ctx.json_output and ctx.debug


def _emit_command_failed(ctx: Context, *, returncode: int) -> int:
    return _emit_error(ctx, "CommandFailed", f"Command failed with exit code {returncode}")


def try_exec_via_ssh_tunnel(
    ctx: Context,
    *,
    command: str,
    bridge_name: Optional[str],
    config: Config,
    timeout_s: int,
    is_tunnel_available_fn: Callable[..., bool],
    run_ssh_command_fn: Callable[..., object],
    run_ssh_command_streaming_fn: Callable[..., int],
) -> Optional[int]:
    """Execute a bridge command via the SSH tunnel path only."""
    reconnect_limit = max(0, int(getattr(config, "tunnel_retries", 0)))
    reconnect_pause = float(getattr(config, "tunnel_retry_pause", 0.0) or 0.0)
    reconnect_state = NotebookBridgeReconnectState(
        reconnect_limit=reconnect_limit,
        reconnect_pause=reconnect_pause,
    )
    resolved_bridge_name = bridge_name
    force_rebuild = False
    opened_once = False
    ssh_execution_started = False
    full_command = _build_remote_command(
        command=command,
        target_dir=str(config.target_dir),
        remote_env=config.remote_env,
    )

    def _require_rebuild(
        bridge: BridgeProfile,
        tunnel_config: object,
        *,
        reason: str,
    ) -> Optional[int]:
        nonlocal force_rebuild

        if not str(bridge.notebook_id or "").strip():
            hint = (
                "Run 'inspire tunnel status' to troubleshoot. "
                "If needed, re-create the bridge via "
                "'inspire notebook ssh <notebook-id> --save-as <name>'."
            )
            return _emit_error(
                ctx,
                "TunnelError",
                "SSH tunnel not available. "
                f"Bridge '{bridge.name}' is not responding "
                "(notebook may be stopped).",
                hint=hint,
            )

        if reconnect_state.reconnect_attempt >= reconnect_limit:
            return _emit_error(
                ctx,
                "TunnelError",
                "SSH tunnel not available",
                hint=(
                    "Auto-rebuild retries exhausted. Run 'inspire tunnel status' and "
                    "retry 'inspire notebook ssh <notebook-id> --save-as <name>'."
                ),
            )

        notebook_id = str(bridge.notebook_id or "").strip()
        if notebook_id:
            try:
                if reconnect_state.web_session is None:
                    reconnect_state.web_session = require_web_session(
                        ctx,
                        hint=(
                            "Automatic tunnel rebuild needs web authentication. "
                            "Set [auth].username and configure password via INSPIRE_PASSWORD "
                            'or [accounts."<username>"].password.'
                        ),
                    )
                notebook_detail = browser_api_module.get_notebook_detail(
                    notebook_id=notebook_id,
                    session=reconnect_state.web_session,
                )
                notebook_status = str((notebook_detail or {}).get("status") or "").strip().upper()
                if notebook_status in _TERMINAL_NOTEBOOK_STATUSES:
                    return _emit_error(
                        ctx,
                        "TunnelError",
                        (
                            "SSH tunnel not available. "
                            f"Bridge '{bridge.name}' notebook '{notebook_id}' "
                            f"is {notebook_status}."
                        ),
                        hint=f"Start it with 'inspire notebook start {notebook_id}' and retry.",
                    )
            except Exception as status_error:  # noqa: BLE001
                logger.debug(
                    "Skipping notebook status preflight bridge=%s notebook_id=%s error=%s",
                    bridge.name,
                    notebook_id,
                    status_error,
                )

        if not ctx.json_output:
            click.echo(
                (
                    f"{reason} "
                    f"(attempt {reconnect_state.reconnect_attempt + 1}/{reconnect_limit})..."
                ),
                err=True,
            )

        result = attempt_notebook_bridge_rebuild(
            state=reconnect_state,
            bridge_name=bridge.name,
            bridge=bridge,
            tunnel_config=tunnel_config,
            session_loader=lambda: require_web_session(
                ctx,
                hint=(
                    "Automatic tunnel rebuild needs web authentication. "
                    "Set [auth].username and configure password via INSPIRE_PASSWORD "
                    'or [accounts."<username>"].password.'
                ),
            ),
            runtime_loader=resolve_ssh_runtime_config,
            rebuild_fn=rebuild_notebook_bridge_profile,
            key_loader=lambda _path=None: load_ssh_public_key_material(),
        )

        if result.status is NotebookBridgeReconnectStatus.REBUILT:
            force_rebuild = False
            return None

        if result.status is NotebookBridgeReconnectStatus.RETRY_LATER:
            if result.pause_seconds > 0:
                time.sleep(result.pause_seconds)
            return None

        # EXHAUSTED or unexpected status — rebuild failed.
        if isinstance(result.error, (ValueError, ConfigError)):
            return _emit_error(
                ctx,
                "TunnelError",
                f"Automatic tunnel rebuild failed: {result.error}",
                hint="Check credentials, SSH key, and notebook status, then retry.",
            )

        return _emit_error(
            ctx,
            "TunnelError",
            (
                f"Automatic tunnel rebuild failed: {result.error}"
                if result.error
                else "SSH tunnel not available"
            ),
            hint="Verify the notebook is RUNNING and retry.",
        )

    def _should_retry_after_disconnect_code(
        *,
        returncode: int,
        tunnel_config: object,
        bridge_name_to_check: str,
    ) -> bool:
        """Retry non-interactive SSH only when 255 also coincides with tunnel loss.

        SSH uses exit code 255 both for transport failures and some command failures.
        To avoid re-running non-idempotent commands incorrectly, require a quick
        tunnel health probe to fail before attempting rebuild/retry.
        """
        if not should_attempt_ssh_reconnect(
            returncode,
            interactive=False,
            allow_non_interactive=True,
        ):
            return False

        try:
            tunnel_still_ready = is_tunnel_available_fn(
                bridge_name=bridge_name_to_check,
                config=tunnel_config,
                retries=0,
                retry_pause=0.0,
                progressive=False,
            )
        except Exception as probe_error:  # noqa: BLE001
            logger.debug("Skipping auto-retry after SSH 255: tunnel probe failed: %s", probe_error)
            return False

        return not tunnel_still_ready

    while True:
        try:
            tunnel_config = load_tunnel_config()
            bridge = tunnel_config.get_bridge(resolved_bridge_name)
            if bridge_name and bridge is None:
                return _emit_error(
                    ctx,
                    "ConfigError",
                    f"Bridge '{bridge_name}' not found.",
                    hint="Run 'inspire tunnel list' to see available bridge profiles.",
                )
            if bridge is None:
                return _emit_error(
                    ctx,
                    "TunnelError",
                    "No bridge configured for SSH execution.",
                    hint="Use 'inspire notebook ssh <notebook-id>' or 'inspire tunnel add' first.",
                )

            resolved_bridge_name = bridge.name
            availability_retries = 0 if force_rebuild else int(config.tunnel_retries)
            availability_pause = 0.0 if force_rebuild else float(config.tunnel_retry_pause)
            tunnel_ready = is_tunnel_available_fn(
                bridge_name=resolved_bridge_name,
                config=tunnel_config,
                retries=availability_retries,
                retry_pause=availability_pause,
                progressive=not force_rebuild,
            )

            if force_rebuild or not tunnel_ready:
                reconnect_error = _require_rebuild(
                    bridge,
                    tunnel_config,
                    reason=(
                        "SSH connection dropped; rebuilding tunnel automatically"
                        if force_rebuild
                        else "Tunnel unavailable; rebuilding automatically"
                    ),
                )
                if reconnect_error is not None:
                    return reconnect_error
                continue

            if ctx.json_output:
                ssh_execution_started = True
                result = run_ssh_command_fn(
                    command=full_command,
                    bridge_name=resolved_bridge_name,
                    timeout=timeout_s,
                    capture_output=True,
                )
                returncode = getattr(result, "returncode", 1)
                if returncode == 0:
                    stdout = getattr(result, "stdout", "") or ""
                    stderr = getattr(result, "stderr", "") or ""
                    emit_output_success(
                        ctx,
                        payload={
                            "status": "success",
                            "method": "ssh_tunnel",
                            "returncode": returncode,
                            "output": stdout + stderr,
                        },
                    )
                    return EXIT_SUCCESS

                if _should_retry_after_disconnect_code(
                    returncode=returncode,
                    tunnel_config=tunnel_config,
                    bridge_name_to_check=resolved_bridge_name,
                ):
                    force_rebuild = True
                    continue

                return _emit_command_failed(ctx, returncode=returncode)

            if _verbose_output(ctx) and not opened_once:
                click.echo("Using SSH tunnel (fast path)")
                click.echo(f"Bridge: {resolved_bridge_name}")
                click.echo(f"Command: {command}")
                click.echo(f"Working dir: {config.target_dir}")
                click.echo("--- Command Output ---")
                opened_once = True

            ssh_execution_started = True
            exit_code = run_ssh_command_streaming_fn(
                command=full_command,
                bridge_name=resolved_bridge_name,
                timeout=timeout_s,
            )
            if _verbose_output(ctx):
                click.echo("--- End Output ---")

            if exit_code == 0:
                click.echo("OK")
                return EXIT_SUCCESS

            if _should_retry_after_disconnect_code(
                returncode=exit_code,
                tunnel_config=tunnel_config,
                bridge_name_to_check=resolved_bridge_name,
            ):
                force_rebuild = True
                continue

            return _emit_command_failed(ctx, returncode=exit_code)

        except TunnelNotAvailableError as e:
            if ssh_execution_started:
                return _emit_error(
                    ctx,
                    "TunnelError",
                    f"SSH execution failed: {e}",
                )
            force_rebuild = True
            continue
        except subprocess.TimeoutExpired:
            emit_output_error(
                ctx,
                error_type="Timeout",
                message=f"Command timed out after {timeout_s}s",
                exit_code=EXIT_TIMEOUT,
                human_lines=[f"Command timed out after {timeout_s}s"],
            )
            return EXIT_TIMEOUT
        except Exception as e:
            if ssh_execution_started:
                return _emit_error(
                    ctx,
                    "SSHExecutionError",
                    f"SSH execution failed: {e}",
                )
            return _emit_error(
                ctx,
                "SSHExecutionError",
                f"SSH execution failed before command start: {e}",
            )


@click.command("exec")
@click.argument("command")
@click.option(
    "timeout",
    "--timeout",
    type=int,
    default=None,
    help="Timeout in seconds (default: config value)",
)
@click.option(
    "bridge",
    "--bridge",
    "-b",
    help="Bridge profile to use for SSH tunnel execution",
)
@pass_context
def exec_command(
    ctx: Context,
    command: str,
    timeout: Optional[int],
    bridge: Optional[str],
) -> None:
    """Execute a command on the Bridge runner via SSH tunnel.

    COMMAND is the shell command to run on Bridge (in INSPIRE_TARGET_DIR).
    Command output (stdout/stderr) is automatically displayed after completion.

    \b
    Examples:
        inspire bridge exec "uv venv .venv"
        inspire bridge exec "pip install torch" --timeout 600
        inspire bridge exec "hostname" --bridge qz-bridge
    """

    try:
        config, _ = Config.from_files_and_env(require_target_dir=True, require_credentials=False)
    except ConfigError as e:
        emit_output_error(
            ctx,
            error_type="ConfigError",
            message=str(e),
            exit_code=EXIT_CONFIG_ERROR,
            human_lines=[f"Configuration error: {e}"],
        )
        sys.exit(EXIT_CONFIG_ERROR)

    exec_timeout = int(timeout) if timeout is not None else int(config.bridge_action_timeout)
    ssh_exit_code = try_exec_via_ssh_tunnel(
        ctx,
        command=command,
        bridge_name=bridge,
        config=config,
        timeout_s=exec_timeout,
        is_tunnel_available_fn=is_tunnel_available,
        run_ssh_command_fn=run_ssh_command,
        run_ssh_command_streaming_fn=run_ssh_command_streaming,
    )
    sys.exit(ssh_exit_code if ssh_exit_code is not None else EXIT_GENERAL_ERROR)
