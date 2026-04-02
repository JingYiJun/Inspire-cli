"""Sync command - Sync code on Bridge over the SSH tunnel.

Usage:
    inspire sync [--remote <remote>]

This command:
1. Syncs code on Bridge via the SSH tunnel
2. Optionally pushes the current branch to the remote (disabled by default)
3. Returns the synced commit SHA

If the git remote is unreachable, use 'inspire bridge scp' to transfer
files directly.
"""

from __future__ import annotations

import concurrent.futures
import logging
import subprocess
import sys
from typing import Optional

import click

from inspire.cli.context import (
    Context,
    pass_context,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_SUCCESS,
)
from inspire.cli.utils.common import json_option
from inspire.cli.utils.notebook_cli import resolve_json_output
from inspire.config import Config, ConfigError
from inspire.bridge.tunnel import (
    BridgeProfile,
    TunnelConfig,
    is_tunnel_available,
    load_tunnel_config,
    save_tunnel_config,
    sync_via_ssh,
    sync_via_ssh_bundle,
)
from inspire.cli.utils.output import (
    emit_error,
    emit_info,
    emit_progress,
    emit_success,
    emit_warning,
)

logger = logging.getLogger(__name__)

_SYNC_BRIDGE_PROBE_TIMEOUT = 2


def get_current_branch() -> str:
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Failed to get current branch: {e.stderr}")
    except FileNotFoundError:
        raise click.ClickException("git command not found. Please install git.")


def get_current_commit_sha(revision: str = "HEAD") -> str:
    """Get the commit SHA for a git revision (default: HEAD)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", revision],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or e.stdout or str(e)
        raise click.ClickException(f"Failed to get commit SHA for '{revision}': {error_msg}")


def get_commit_message(revision: str = "HEAD") -> str:
    """Get the commit message (first line) for a git revision."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s", revision],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def has_uncommitted_changes() -> bool:
    """Check if there are uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def push_to_remote(branch: str, remote: str, *, show_progress: bool = False) -> None:
    """Push the branch to the remote."""
    if show_progress:
        click.echo(f"Pushing {branch} to {remote}...")
    try:
        result = subprocess.run(
            ["git", "push", remote, branch],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stderr:
            logger.debug("git push stderr: %s", result.stderr)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr or e.stdout or str(e)
        raise click.ClickException(f"Failed to push to {remote}: {error_msg}")


def _ordered_bridges_for_sync(tunnel_config: TunnelConfig) -> list[BridgeProfile]:
    """Return configured bridges with the default bridge first, then config order."""
    bridges = tunnel_config.list_bridges()
    if not bridges:
        return []

    default_bridge = tunnel_config.default_bridge
    return sorted(
        bridges,
        key=lambda bridge: (0 if bridge.name == default_bridge else 1),
    )


def _effective_push_mode(
    *,
    no_push: bool,
    push_mode: Optional[str],
) -> str:
    """Resolve git push behavior before sync."""
    if no_push:
        return "skip"
    if push_mode:
        return push_mode
    return "skip"


def _probe_live_bridges(
    *,
    tunnel_config: TunnelConfig,
    config: Config,
    candidate_bridges: list[BridgeProfile],
) -> tuple[list[BridgeProfile], list[str]]:
    """Probe all candidate bridges in parallel and return the live subset in order."""
    tried_bridges = [bridge.name for bridge in candidate_bridges]
    if not candidate_bridges:
        return [], tried_bridges

    def _probe(bridge: BridgeProfile) -> bool:
        return is_tunnel_available(
            bridge_name=bridge.name,
            config=tunnel_config,
            retries=0,
            retry_pause=0.0,
            progressive=False,
            probe_timeout=_SYNC_BRIDGE_PROBE_TIMEOUT,
        )

    live_by_name: dict[str, bool] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(candidate_bridges)) as pool:
        future_map = {pool.submit(_probe, bridge): bridge.name for bridge in candidate_bridges}
        for future in concurrent.futures.as_completed(future_map):
            bridge_name = future_map[future]
            try:
                live_by_name[bridge_name] = future.result()
            except Exception:
                live_by_name[bridge_name] = False

    live_bridges = [bridge for bridge in candidate_bridges if live_by_name.get(bridge.name, False)]
    return live_bridges, tried_bridges


def _eligible_live_bridges(
    *,
    source: str,
    live_bridges: list[BridgeProfile],
) -> list[BridgeProfile]:
    """Return live bridges eligible for the requested sync source."""
    if source == "remote":
        return [bridge for bridge in live_bridges if bridge.has_internet]
    return list(live_bridges)


def _is_locale_warning(line: str) -> bool:
    """Return True when *line* is a known locale warning we intentionally hide."""
    return "setlocale:" in line and "cannot change locale" in line


def _normalize_sync_error(raw_error: object) -> str:
    """Normalize sync stderr/stdout text for user-facing reporting.

    - Drops locale warning chatter that does not affect sync behavior.
    - Removes duplicate lines while preserving first-seen order.
    """
    text = str(raw_error or "").strip()
    if not text:
        return "Unknown error"

    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _is_locale_warning(line):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)

    if not lines:
        return "Unknown error"
    return "\n".join(lines)


def _is_ff_divergence_error(error_text: str) -> bool:
    """Detect fast-forward divergence errors from git output."""
    lowered = error_text.lower()
    return (
        "not possible to fast-forward" in lowered
        or "diverging branches can't be fast-forwarded" in lowered
    )


def _summarize_sync_failure(
    *,
    raw_error: object,
    branch: str,
    remote: str,
) -> tuple[str, Optional[str], str]:
    """Build concise sync failure message + hint + normalized details."""
    normalized = _normalize_sync_error(raw_error)

    if _is_ff_divergence_error(normalized):
        message = f"Branch '{branch}' on Bridge diverged and cannot be fast-forwarded."
        hint = (
            f"Reconcile branch history (merge/rebase) and retry sync. "
            f"If you expected a fresh tip, push '{branch}' to '{remote}' first. "
            "To overwrite Bridge with your selected commit, rerun with --force."
        )
        return message, hint, normalized

    first_line = normalized.splitlines()[0].strip() if normalized else "Unknown error"
    return first_line, None, normalized


def _is_sync_timeout_error(raw_error: object) -> bool:
    """Return True when sync failure text indicates a timeout."""
    normalized = _normalize_sync_error(raw_error).lower()
    return "timed out after" in normalized


def sync_via_tunnel(
    ctx: Context,
    config: Config,
    *,
    branch: str,
    commit_sha: str,
    commit_msg: str,
    remote: str,
    timeout: int,
    force: bool = False,
    offline_bundle: bool = False,
    bridge_name: Optional[str] = None,
    fallback_bridge_names: Optional[list[str]] = None,
    tunnel_config=None,
) -> int:
    """Sync code via SSH tunnel (fast path)."""
    initial_bridge_name = bridge_name
    bridge_sequence = [initial_bridge_name] if initial_bridge_name else [None]
    if fallback_bridge_names:
        bridge_sequence.extend(fallback_bridge_names)

    result: dict[str, object] = {"success": False, "synced_sha": None, "error": "Unknown error"}
    last_bridge_name = bridge_sequence[0]

    for index, current_bridge_name in enumerate(bridge_sequence):
        last_bridge_name = current_bridge_name
        if ctx.debug and not ctx.json_output:
            if current_bridge_name:
                emit_progress(ctx, f"Syncing via SSH tunnel (bridge: {current_bridge_name})...")
            else:
                emit_progress(ctx, "Syncing via SSH tunnel...")

        if offline_bundle:
            result = sync_via_ssh_bundle(
                target_dir=config.target_dir,
                branch=branch,
                commit_sha=commit_sha,
                force=force,
                bridge_name=current_bridge_name,
                config=tunnel_config,
                timeout=timeout,
            )
        else:
            result = sync_via_ssh(
                target_dir=config.target_dir,
                branch=branch,
                commit_sha=commit_sha,
                remote=remote,
                force=force,
                bridge_name=current_bridge_name,
                config=tunnel_config,
                timeout=timeout,
            )

        if result.get("success"):
            break

        has_next_bridge = index < len(bridge_sequence) - 1
        if not has_next_bridge or not _is_sync_timeout_error(result.get("error")):
            break

        next_bridge_name = bridge_sequence[index + 1]
        if not ctx.json_output:
            from_bridge = current_bridge_name or "<default>"
            to_bridge = next_bridge_name or "<default>"
            emit_warning(
                ctx,
                f"sync timed out on bridge '{from_bridge}'; retrying with '{to_bridge}'.",
            )

    if result.get("success"):
        if (
            last_bridge_name
            and tunnel_config is not None
            and last_bridge_name != initial_bridge_name
            and tunnel_config.default_bridge != last_bridge_name
        ):
            tunnel_config.default_bridge = last_bridge_name
            try:
                save_tunnel_config(tunnel_config)
            except Exception as e:
                if not ctx.json_output:
                    emit_warning(
                        ctx,
                        f"sync succeeded but failed to persist updated default bridge '{last_bridge_name}': {e}",
                    )
        synced_sha = result.get("synced_sha") or commit_sha[:7]
        bundle_mode = result.get("bundle_mode") if offline_bundle else None
        bundle_base_sha = result.get("bundle_base_sha") if offline_bundle else None
        payload: dict[str, object] = {
            "status": "success",
            "method": "ssh_bundle" if offline_bundle else "ssh_tunnel",
            "branch": branch,
            "remote": remote,
            "commit": commit_sha[:7],
            "commit_full": commit_sha,
            "synced_sha": synced_sha,
            "message": commit_msg,
            "target_dir": config.target_dir,
        }
        if last_bridge_name:
            payload["bridge_name"] = last_bridge_name
        if bundle_mode:
            payload["bundle_mode"] = bundle_mode
        if bundle_base_sha:
            payload["bundle_base_sha"] = bundle_base_sha

        if ctx.debug and not ctx.json_output:
            emit_info(ctx, f"Synced branch '{branch}' ({synced_sha[:7]}) to {config.target_dir}")
            emit_info(ctx, f"  Commit: {commit_msg}")
            if last_bridge_name:
                emit_info(ctx, f"  Bridge: {last_bridge_name}")
            if offline_bundle:
                mode_suffix = f", {bundle_mode}" if bundle_mode else ""
                emit_info(ctx, f"  Method: SSH tunnel (offline bundle{mode_suffix})")
            else:
                emit_info(ctx, "  Method: SSH tunnel (fast)")
        else:
            emit_success(
                ctx,
                payload=payload,
                text=f"synced {synced_sha[:7]} via {'ssh-bundle' if offline_bundle else 'ssh'}",
            )
        return EXIT_SUCCESS

    message, hint, details = _summarize_sync_failure(
        raw_error=result.get("error"),
        branch=branch,
        remote=remote,
    )

    human_lines: list[str] = []
    if ctx.debug and details and details != message:
        human_lines.extend(["Details:", details])
    emit_error(
        ctx,
        error_type="SyncError",
        message=f"Sync failed: {message}",
        exit_code=EXIT_GENERAL_ERROR,
        hint=hint,
        human_lines=human_lines if human_lines else None,
    )
    return EXIT_GENERAL_ERROR


@click.command()
@click.option(
    "--remote",
    "-r",
    default=None,
    help="Git remote to push to (default: from INSPIRE_DEFAULT_REMOTE or 'origin')",
)
@click.option(
    "--no-push",
    is_flag=True,
    help="Skip git push before sync (default behavior; same as --push-mode skip)",
)
@click.option(
    "--allow-dirty",
    is_flag=True,
    help="Allow sync with uncommitted changes (syncs committed branch tip only)",
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Force sync mode: imply --allow-dirty, and hard-reset diverged Bridge branch on SSH sync"
    ),
)
@click.option(
    "--timeout",
    default=120,
    help="Timeout in seconds when waiting for sync (default: 120)",
)
@click.option(
    "--source",
    type=click.Choice(["remote", "bundle"], case_sensitive=False),
    default="bundle",
    show_default=True,
    help="For SSH transport: choose sync source (bundle is default and recommended)",
)
@click.option(
    "--push-mode",
    type=click.Choice(["required", "best-effort", "skip"], case_sensitive=False),
    default=None,
    help="Git push policy before sync (default: skip; use required/best-effort to enable push)",
)
@json_option
@pass_context
def sync(
    ctx: Context,
    remote: Optional[str],
    no_push: bool,
    allow_dirty: bool,
    force: bool,
    timeout: int,
    source: str,
    push_mode: Optional[str],
    json_output: bool = False,
) -> None:
    json_output = resolve_json_output(ctx, json_output)
    """Sync local code to the Bridge shared filesystem.

    This command syncs to Bridge over the SSH tunnel.
    Git push is skipped by default unless --push-mode is set:
    - remote: fetch from the git remote over the selected live bridge
    - bundle: upload a local git bundle over the selected live bridge (default)

    \b
    Examples:
        inspire sync                          # Sync current branch via SSH tunnel
        inspire sync --remote upstream        # Sync via upstream remote
        inspire sync --source bundle          # Force local bundle sync over SSH
        inspire sync --push-mode required     # Push before sync (fail if push fails)
        inspire sync --push-mode best-effort  # Continue even if git push fails
        inspire sync --no-push                # Explicitly skip git push (default)
        inspire sync --allow-dirty            # Sync committed branch tip even if worktree is dirty
        inspire sync --allow-dirty --no-push --source bundle --force
                                             # Also force-resets Bridge branch to selected commit

    \b
    Environment variables:
        INSPIRE_DEFAULT_REMOTE    Default git remote (default: origin)
        INSPIRE_TARGET_DIR        Target directory on Bridge (required)
    """
    try:
        config, _ = Config.from_files_and_env(require_target_dir=True, require_credentials=False)
    except ConfigError as e:
        emit_error(
            ctx,
            error_type="ConfigError",
            message=f"Configuration error: {e}",
            exit_code=EXIT_CONFIG_ERROR,
        )
        sys.exit(EXIT_CONFIG_ERROR)

    # Determine current branch
    branch = get_current_branch()

    # Determine remote
    if remote is None:
        remote = config.default_remote

    source = source.lower().strip()
    push_mode = push_mode.lower().strip() if push_mode else None

    if no_push and push_mode and push_mode != "skip":
        emit_error(
            ctx,
            error_type="ValidationError",
            message="--no-push conflicts with --push-mode values other than 'skip'",
            exit_code=EXIT_CONFIG_ERROR,
        )
        sys.exit(EXIT_CONFIG_ERROR)

    tunnel_config = None
    selected_bridge = None
    use_offline_bundle = False
    candidate_bridges: list[BridgeProfile] = []
    fallback_bridge_names: list[str] = []
    tunnel_config = load_tunnel_config()
    candidate_bridges = _ordered_bridges_for_sync(tunnel_config)
    if not candidate_bridges:
        emit_error(
            ctx,
            error_type="TunnelUnavailable",
            message="No bridge configured for SSH sync",
            exit_code=EXIT_CONFIG_ERROR,
            hint="Use 'inspire tunnel list' or 'inspire notebook ssh <id>' first.",
        )
        sys.exit(EXIT_CONFIG_ERROR)

    live_bridges, tried_bridges = _probe_live_bridges(
        tunnel_config=tunnel_config,
        config=config,
        candidate_bridges=candidate_bridges,
    )
    if not live_bridges:
        tried_csv = ", ".join(tried_bridges)
        emit_error(
            ctx,
            error_type="TunnelUnavailable",
            message=f"SSH tunnel is not available for any configured bridge (tried: {tried_csv})",
            exit_code=EXIT_GENERAL_ERROR,
            hint="Run 'inspire tunnel status' to troubleshoot the tunnel.",
        )
        sys.exit(EXIT_GENERAL_ERROR)

    eligible_bridges = _eligible_live_bridges(source=source, live_bridges=live_bridges)
    if not eligible_bridges and source == "remote":
        first_live = live_bridges[0]
        emit_error(
            ctx,
            error_type="ValidationError",
            message=f"Bridge '{first_live.name}' has no internet; remote source is unavailable",
            exit_code=EXIT_CONFIG_ERROR,
            hint="Use '--source bundle' for no-internet bridges.",
        )
        sys.exit(EXIT_CONFIG_ERROR)

    selected_bridge = eligible_bridges[0]
    fallback_bridge_names = [bridge.name for bridge in eligible_bridges[1:]]
    use_offline_bundle = source == "bundle"
    if ctx.debug and not ctx.json_output:
        emit_info(ctx, f"Using bridge '{selected_bridge.name}' for sync.")
        if use_offline_bundle:
            emit_info(ctx, "Using offline bundle sync path (--source bundle).")
        else:
            emit_info(ctx, "Using remote git sync path (--source remote).")

    # Check for uncommitted changes
    if has_uncommitted_changes():
        if not (allow_dirty or force):
            hint = (
                "Commit/stash changes, or pass --allow-dirty to sync committed HEAD only. "
                "Use --force only when you also want force-sync behavior "
                "(implies --allow-dirty and may overwrite a diverged Bridge branch)."
            )
            emit_error(
                ctx,
                error_type="ValidationError",
                message="Uncommitted changes detected",
                exit_code=EXIT_GENERAL_ERROR,
                hint=hint,
            )
            sys.exit(EXIT_GENERAL_ERROR)

        dirty_override_flag = "--allow-dirty" if allow_dirty else "--force"
        if not ctx.json_output:
            emit_warning(
                ctx,
                f"Uncommitted changes detected; syncing committed tip of '{branch}' only ({dirty_override_flag}).",
            )

    commit_sha = get_current_commit_sha(branch)
    commit_msg = get_commit_message(branch)
    effective_push_mode = _effective_push_mode(
        no_push=no_push,
        push_mode=push_mode,
    )

    if effective_push_mode != "skip":
        try:
            push_to_remote(branch, remote, show_progress=ctx.debug and not ctx.json_output)
        except click.ClickException as e:
            if effective_push_mode == "best-effort":
                if not ctx.json_output:
                    emit_warning(
                        ctx,
                        f"{e}. Continuing because push mode is best-effort.",
                    )
            else:
                if ctx.json_output:
                    emit_error(
                        ctx,
                        error_type="GitError",
                        message=str(e),
                        exit_code=EXIT_GENERAL_ERROR,
                    )
                    sys.exit(EXIT_GENERAL_ERROR)
                raise
    elif ctx.debug and not ctx.json_output:
        emit_info(ctx, "Skipping git push before sync.")

    exit_code = sync_via_tunnel(
        ctx,
        config,
        branch=branch,
        commit_sha=commit_sha,
        commit_msg=commit_msg,
        remote=remote,
        timeout=timeout,
        force=force,
        offline_bundle=use_offline_bundle,
        bridge_name=selected_bridge.name,
        fallback_bridge_names=fallback_bridge_names,
        tunnel_config=tunnel_config,
    )
    sys.exit(exit_code)
