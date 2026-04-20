"""Notebook subcommands."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Optional

import click

from .notebook_create_flow import maybe_run_post_start, run_notebook_create
from .notebook_lookup import (
    _ZERO_WORKSPACE_ID,
    _list_notebooks_for_workspace,
    _normalize_notebook_id,
    _resolve_notebook_id,
    _sort_notebook_items_by_tunnel_priority,
    _try_get_current_user_ids,
    _unique_workspace_ids,
)
from .notebook_presenters import _print_notebook_detail, _print_notebook_list
from .notebook_ssh_flow import run_notebook_ssh
from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_CONFIG_ERROR,
    pass_context,
)
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.notebook_cli import (
    get_base_url,
    load_config,
    require_web_session,
    resolve_json_output,
)
from inspire.cli.utils.notebook_post_start import (
    NO_WAIT_POST_START_WARNING,
    resolve_notebook_post_start_spec,
)
from inspire.config import ConfigError
from inspire.config.workspaces import select_workspace_id
from inspire.bridge.tunnel import load_tunnel_config
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web import session as web_session_module
from inspire.platform.web.browser_api import NotebookFailedError


@click.command("create")
@click.option(
    "--name",
    "-n",
    help="Notebook name (auto-generated if omitted)",
)
@click.option(
    "--workspace",
    help=(
        'Workspace alias or ID. Common aliases from [accounts."<username>".workspaces] config: '
        "'cpu' (CPU workloads), 'gpu' (H100/H200), 'internet' (RTX 4090 with internet). "
        "Use --workspace-id for explicit UUID."
    ),
)
@click.option(
    "--workspace-id",
    help="Workspace ID override (escape hatch; overrides auto-selection)",
)
@click.option(
    "--resource",
    "-r",
    default=None,
    help="Resource spec (e.g., 1xH200, 4xH100, 4CPU) (default from config [notebook].resource or [defaults].resource)",
)
@click.option(
    "--compute-group",
    default=None,
    help="Explicit compute group name or logic_compute_group_id override",
)
@click.option(
    "--project",
    "-p",
    default=None,
    help="Project name or ID (default from config [notebook].project_id or [defaults].project_order)",
)
@click.option(
    "--image",
    "-i",
    default=None,
    help=(
        "Image name/URL (default from config [notebook].image or [defaults].image; prompts "
        "interactively if still omitted)"
    ),
)
@click.option(
    "--shm-size",
    type=int,
    default=None,
    help="Shared memory size in GB (default from config [notebook].shm_size or [defaults].shm_size, else 32)",
)
@click.option(
    "--auto-stop/--no-auto-stop",
    default=False,
    help="Auto-stop when idle",
)
@click.option(
    "--auto/--no-auto",
    default=True,
    help="Auto-select best available compute group based on availability (default: auto)",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help=(
        "Wait for notebook to reach RUNNING status "
        "(default: enabled; still required when a post-start action is configured)"
    ),
)
@click.option(
    "--post-start",
    type=str,
    default=None,
    help="Post-start action after RUNNING: none or a shell command",
)
@click.option(
    "--post-start-script",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Local shell script to upload and run in the notebook after RUNNING",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@click.option(
    "--priority",
    type=click.IntRange(1, 10),
    default=None,
    help="Task priority (1-10, default from config [notebook].priority or [defaults].priority or 6)",
)
@pass_context
def create_notebook_cmd(
    ctx: Context,
    name: Optional[str],
    workspace: Optional[str],
    workspace_id: Optional[str],
    resource: Optional[str],
    compute_group: Optional[str],
    project: Optional[str],
    image: Optional[str],
    shm_size: Optional[int],
    auto_stop: bool,
    auto: bool,
    wait: bool,
    post_start: Optional[str],
    post_start_script: Optional[Path],
    json_output: bool,
    priority: Optional[int],
) -> None:
    """Create a new interactive notebook instance.

    \b
    Examples:
        inspire notebook create                     # Interactive mode, auto-select GPU
        inspire notebook create -r 1xH200           # 1 GPU H200
        inspire notebook create -r 4xH100 -n mytest # 4 GPUs H100
        inspire notebook create -r 4x               # 4 GPUs, auto-select type
        inspire notebook create -r 8x               # 8 GPUs (full node), auto-select type
        inspire notebook create -r 4CPU             # 4 CPUs
        inspire notebook create -r 1xH100 --shm-size 64  # With 64GB shared memory
        inspire notebook create --no-auto -r 1xH200 # Disable auto-select
        inspire notebook create --post-start 'bash /workspace/bootstrap.sh'
        inspire notebook create --post-start-script scripts/notebook_bootstrap.sh
        inspire notebook create --post-start none --no-wait
        inspire notebook create --priority 5        # Set task priority to 5
    """
    if post_start and post_start_script:
        raise click.UsageError("Use either --post-start or --post-start-script, not both.")

    project_explicit = bool(project)

    run_notebook_create(
        ctx,
        name=name,
        workspace=workspace,
        workspace_id=workspace_id,
        resource=resource,
        compute_group_name=compute_group,
        project=project,
        image=image,
        shm_size=shm_size,
        auto_stop=auto_stop,
        auto=auto,
        wait=wait,
        post_start=post_start,
        post_start_script=post_start_script,
        json_output=json_output,
        priority=priority,
        project_explicit=project_explicit,
    )


@click.command("stop")
@click.argument("notebook")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def stop_notebook_cmd(
    ctx: Context,
    notebook: str,
    json_output: bool,
) -> None:
    """Stop a running notebook instance.

    \b
    Examples:
        inspire notebook stop abc123-def456
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Stopping notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    base_url = get_base_url()
    config = load_config(ctx)
    notebook_id, _ = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    try:
        result = browser_api_module.stop_notebook(notebook_id=notebook_id, session=session)
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to stop notebook: {e}", EXIT_API_ERROR)
        return

    if json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "notebook_id": notebook_id,
                    "status": "stopping",
                    "result": result,
                }
            )
        )
        return

    click.echo(f"Notebook '{notebook_id}' is being stopped.")
    click.echo(f"Use 'inspire notebook status {notebook_id}' to check status.")


@click.command("start")
@click.argument("notebook")
@click.option(
    "--wait/--no-wait",
    default=False,
    help="Wait for notebook to reach RUNNING status (still required for post-start actions)",
)
@click.option(
    "--post-start",
    type=str,
    default=None,
    help="Post-start action after RUNNING: none or a shell command",
)
@click.option(
    "--post-start-script",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    default=None,
    help="Local shell script to upload and run in the notebook after RUNNING",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def start_notebook_cmd(
    ctx: Context,
    notebook: str,
    wait: bool,
    post_start: Optional[str],
    post_start_script: Optional[Path],
    json_output: bool,
) -> None:
    """Start a stopped notebook instance.

    \b
    Examples:
        inspire notebook start 78822a57-3830-44e7-8d45-e8b0d674fc44
        inspire notebook start ring-8h100-test
        inspire notebook start ring-8h100-test --wait
        inspire notebook start ring-8h100-test --post-start 'bash /workspace/bootstrap.sh'
        inspire notebook start ring-8h100-test --post-start-script scripts/notebook_bootstrap.sh
        inspire notebook start ring-8h100-test --post-start none
    """
    if post_start and post_start_script:
        raise click.UsageError("Use either --post-start or --post-start-script, not both.")

    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Starting notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    base_url = get_base_url()
    config = load_config(ctx)
    try:
        post_start_spec = resolve_notebook_post_start_spec(
            config=config,
            post_start=post_start,
            post_start_script=post_start_script,
        )
    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
        return
    except ValueError as e:
        _handle_error(ctx, "ValidationError", str(e), EXIT_CONFIG_ERROR)
        return

    notebook_id, _ = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    try:
        result = browser_api_module.start_notebook(notebook_id=notebook_id, session=session)
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to start notebook: {e}", EXIT_API_ERROR)
        return

    if not json_output:
        click.echo(f"Notebook '{notebook_id}' is being started.")

    notebook_detail = None
    if wait or post_start_spec is not None:
        if not wait and post_start_spec is not None and not json_output:
            click.echo(NO_WAIT_POST_START_WARNING, err=True)
        if not json_output:
            click.echo("Waiting for notebook to reach RUNNING status...")
        try:
            notebook_detail = browser_api_module.wait_for_notebook_running(
                notebook_id=notebook_id, session=session
            )
            if not json_output:
                click.echo("Notebook is now RUNNING.")
        except NotebookFailedError as e:
            _handle_error(
                ctx,
                "NotebookFailed",
                f"Notebook failed to start: {e}",
                EXIT_API_ERROR,
                hint=e.events or "Check Events tab in web UI for details.",
            )
            return
        except TimeoutError as e:
            _handle_error(
                ctx,
                "Timeout",
                f"Timed out waiting for notebook to reach RUNNING: {e}",
                EXIT_API_ERROR,
            )
            return

    if notebook_detail and post_start_spec is not None:
        quota = notebook_detail.get("quota") or {}
        gpu_count = quota.get("gpu_count", 0) or 0
        maybe_run_post_start(
            ctx,
            notebook_id=notebook_id,
            session=session,
            post_start_spec=post_start_spec,
            gpu_count=gpu_count,
            json_output=json_output,
        )

    if json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "notebook_id": notebook_id,
                    "status": "starting",
                    "result": result,
                }
            )
        )
        return

    click.echo(f"Use 'inspire notebook status {notebook_id}' to check status.")


@click.command("status")
@click.argument("notebook")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def notebook_status(
    ctx: Context,
    notebook: str,
    json_output: bool,
) -> None:
    """Get status of a notebook instance.

    \b
    Examples:
        inspire notebook status notebook-abc-123
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Notebook status requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    base_url = get_base_url()

    config = load_config(ctx)
    notebook_id, _ = _resolve_notebook_id(
        ctx,
        session=session,
        config=config,
        base_url=base_url,
        identifier=notebook,
        json_output=json_output,
    )

    try:
        data = web_session_module.request_json(
            session,
            "GET",
            f"{base_url}/api/v1/notebook/{notebook_id}",
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except ValueError as e:
        message = str(e)
        if "API returned 404" in message:
            _handle_error(
                ctx,
                "NotFound",
                f"Notebook instance '{notebook_id}' not found",
                EXIT_API_ERROR,
            )
        else:
            _handle_error(ctx, "APIError", message, EXIT_API_ERROR)
        return
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
        return

    if data.get("code") == 0:
        notebook = data.get("data", {})
        if json_output:
            click.echo(json_formatter.format_json(notebook))
        else:
            _print_notebook_detail(notebook)
        return

    _handle_error(
        ctx,
        "APIError",
        data.get("message", "Unknown error"),
        EXIT_API_ERROR,
    )
    return


@click.command("list")
@click.option(
    "--workspace",
    help=(
        'Workspace alias or ID. Common aliases from [accounts."<username>".workspaces] config: '
        "'cpu' (CPU workloads), 'gpu' (H100/H200), 'internet' (RTX 4090 with internet). "
        "Use --workspace-id for explicit UUID."
    ),
)
@click.option(
    "--workspace-id",
    help="Workspace ID (defaults to configured workspace)",
)
@click.option(
    "--all",
    "-a",
    "show_all",
    is_flag=True,
    help="Show all notebooks (not just your own)",
)
@click.option(
    "--all-workspaces",
    "-A",
    is_flag=True,
    help="List notebooks across all configured workspaces (cpu/gpu/internet)",
)
@click.option(
    "--limit",
    "-n",
    type=int,
    default=20,
    show_default=True,
    help="Max number of notebooks to show",
)
@click.option(
    "--tunneled",
    is_flag=True,
    help="Show only notebooks with active SSH tunnels",
)
@click.option(
    "--status",
    "-s",
    multiple=True,
    help="Filter by status (e.g. RUNNING, STOPPED). Repeatable.",
)
@click.option(
    "--name",
    "keyword",
    default="",
    help="Filter by notebook name (keyword search)",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@click.option(
    "--columns",
    "-c",
    default="name,status,resource",
    help="Comma-separated columns to display (name,status,resource,id,created,gpu,cpu,memory,image,project,workspace,node,uptime,tunnel)",
)
@pass_context
def list_notebooks(
    ctx: Context,
    workspace: Optional[str],
    workspace_id: Optional[str],
    show_all: bool,
    all_workspaces: bool,
    limit: int,
    status: tuple[str, ...],
    keyword: str,
    json_output: bool,
    columns: str,
    tunneled: bool,
) -> None:
    """List notebook/interactive instances.

    By default, shows recent notebooks with those having active tunnels listed first.
    Use --tunneled to show only notebooks with SSH tunnels.

    \b
    Examples:
        inspire notebook list
        inspire notebook list --all
        inspire notebook list -n 10
        inspire notebook list --tunneled -n 10
        inspire notebook list -s RUNNING
        inspire notebook list -s RUNNING -s STOPPED
        inspire notebook list --name my-notebook
        inspire notebook list --workspace gpu -s RUNNING -n 5
        inspire notebook list --all-workspaces
        inspire notebook list --json
        inspire notebook list -c name,status,tunnel
        inspire notebook list -c name,status,gpu,uptime
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Listing notebooks requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )
    config = load_config(ctx)

    workspace_ids: list[str] = []
    if workspace_id:
        workspace_ids = [workspace_id]
    elif workspace:
        try:
            resolved = select_workspace_id(config, explicit_workspace_name=workspace)
        except ConfigError as e:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return
        if resolved:
            workspace_ids = [resolved]
    elif all_workspaces:
        candidates: list[str] = []
        for ws_id in (
            config.workspace_cpu_id,
            config.workspace_gpu_id,
            config.workspace_internet_id,
        ):
            if ws_id:
                candidates.append(ws_id)
        if config.workspaces:
            candidates.extend(config.workspaces.values())
        if getattr(session, "workspace_id", None):
            candidates.append(str(session.workspace_id))

        workspace_ids = _unique_workspace_ids(candidates)
        for ws_id in workspace_ids:
            try:
                select_workspace_id(config, explicit_workspace_id=ws_id)
            except ConfigError as e:
                _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
                return

    if not workspace_ids:
        try:
            resolved = select_workspace_id(
                config,
                legacy_workspace_id=config.job_workspace_id
                or getattr(config, "default_workspace_id", None)
                or getattr(config, "notebook_workspace_id", None),
            )
        except ConfigError as e:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return

        resolved = resolved or getattr(session, "workspace_id", None)
        resolved = None if resolved == _ZERO_WORKSPACE_ID else resolved
        if not resolved:
            _handle_error(
                ctx,
                "ConfigError",
                "No workspace_id configured or provided.",
                EXIT_CONFIG_ERROR,
                hint=(
                    "Use --workspace-id, pass --workspace cpu/gpu/internet, or set "
                    '[accounts."<username>".workspaces].cpu/'
                    '[accounts."<username>".workspaces].gpu in config.toml.'
                ),
            )
            return
        workspace_ids = [str(resolved)]

    base_url = get_base_url()

    user_ids = [] if show_all else _try_get_current_user_ids(session, base_url=base_url)

    all_items: list[dict] = []
    successful_workspaces = 0
    # When filtering by tunneled, fetch more to ensure we get enough after filtering
    fetch_limit = limit * 5 if tunneled else limit
    for ws_id in workspace_ids:
        status_filter = [s.upper() for s in status] if status else []
        try:
            items = _list_notebooks_for_workspace(
                session,
                base_url=base_url,
                workspace_id=ws_id,
                user_ids=user_ids,
                keyword=keyword,
                page_size=fetch_limit,
                status=status_filter,
            )
            all_items.extend(items)
            successful_workspaces += 1
        except ValueError as e:
            if len(workspace_ids) == 1:
                _handle_error(
                    ctx,
                    "APIError",
                    str(e),
                    EXIT_API_ERROR,
                    hint="Check auth and proxy configuration.",
                )
                return
            if not ctx.json_output:
                click.echo(f"Warning: workspace {ws_id} failed: {e}", err=True)
            continue
        except Exception as e:
            if len(workspace_ids) == 1:
                _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
                return
            if not ctx.json_output:
                click.echo(f"Warning: workspace {ws_id} failed: {e}", err=True)
            continue

    # Only report a failure when every workspace call raised. An empty result set
    # from successful calls (e.g. no notebooks match --status RUNNING) is not an
    # error and should fall through to render an empty list.
    if successful_workspaces == 0 and len(workspace_ids) > 1:
        _handle_error(
            ctx,
            "APIError",
            "Failed to list notebooks from configured workspaces.",
            EXIT_API_ERROR,
        )
        return

    # Filter and sort by tunnel status
    tunnel_config = load_tunnel_config()
    tunneled_ids = {
        bridge.notebook_id for bridge in tunnel_config.list_bridges() if bridge.notebook_id
    }

    if tunneled:
        # Show only notebooks with tunnels
        all_items = [
            item
            for item in all_items
            if _normalize_notebook_id(item.get("notebook_id") or item.get("id", "")) in tunneled_ids
        ]
        # Sort by created_at descending (most recent first)
        all_items = sorted(
            all_items, key=lambda item: str(item.get("created_at") or ""), reverse=True
        )
        # Apply limit after filtering
        all_items = all_items[:limit]
    else:
        # Sort by tunnel priority (notebooks with tunnels first)
        all_items = _sort_notebook_items_by_tunnel_priority(all_items, tunneled_ids)

    _print_notebook_list(all_items, json_output, columns=columns, tunnel_config=tunnel_config)


@click.command("ssh")
@click.argument("notebook")
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for notebook to reach RUNNING status",
)
@click.option(
    "--pubkey",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help=(
        "SSH public key path to authorize (defaults to ~/.ssh/id_ed25519.pub or ~/.ssh/id_rsa.pub)"
    ),
)
@click.option(
    "--alias",
    "aliases",
    multiple=True,
    help=("Add one or more reusable bridge aliases (repeatable; defaults to the notebook name)"),
)
@click.option(
    "--save-as",
    "save_as",
    multiple=True,
    help="Alias for --alias; preferred name for saving reusable bridge profiles",
)
@click.option(
    "--port",
    default=31337,
    show_default=True,
    help="rtunnel server listen port inside notebook",
)
@click.option(
    "--ssh-port",
    default=22222,
    show_default=True,
    help="sshd port inside notebook",
)
@click.option(
    "--command",
    help="Optional remote command to run (if omitted, opens an interactive shell)",
)
@click.option(
    "--rtunnel-bin",
    help="Path to pre-cached rtunnel binary (e.g., /inspire/.../rtunnel)",
)
@click.option(
    "--rtunnel-upload-policy",
    type=click.Choice(["auto", "never", "always"], case_sensitive=False),
    default=None,
    help="Rtunnel upload fallback: auto (default), never, or always",
)
@click.option(
    "--debug-playwright",
    is_flag=True,
    help="Run browser automation with visible window for debugging",
)
@click.option(
    "--timeout",
    "setup_timeout",
    default=300,
    show_default=True,
    help="Timeout in seconds for rtunnel setup to complete",
)
@click.argument("ssh_command", nargs=-1, type=click.UNPROCESSED)
@pass_context
def ssh_notebook_cmd(
    ctx: Context,
    notebook: str,
    wait: bool,
    pubkey: Optional[str],
    aliases: tuple[str, ...],
    save_as: tuple[str, ...],
    port: int,
    ssh_port: int,
    command: Optional[str],
    rtunnel_bin: Optional[str],
    rtunnel_upload_policy: Optional[str],
    debug_playwright: bool,
    setup_timeout: int,
    ssh_command: tuple[str, ...],
) -> None:
    """SSH into a running notebook instance via rtunnel ProxyCommand.

    \b
    Examples:
        inspire notebook ssh abc123
        inspire notebook ssh abc123 --save-as mybridge
        inspire notebook ssh abc123 --command "echo hello"
        inspire notebook ssh abc123 -- echo 'connected'
        inspire notebook ssh abc123 -- python train.py --epochs 100
    """
    if ssh_command and command:
        raise click.UsageError("Provide a remote command via --command or after '--', not both.")
    if ssh_command:
        command = shlex.join(ssh_command)
    merged_aliases = aliases + save_as
    run_notebook_ssh(
        ctx,
        notebook_id=notebook,
        wait=wait,
        pubkey=pubkey,
        aliases=merged_aliases,
        port=port,
        ssh_port=ssh_port,
        command=command,
        rtunnel_bin=rtunnel_bin,
        rtunnel_upload_policy=rtunnel_upload_policy,
        debug_playwright=debug_playwright,
        setup_timeout=setup_timeout,
    )


__all__ = [
    "create_notebook_cmd",
    "list_notebooks",
    "notebook_status",
    "ssh_notebook_cmd",
    "start_notebook_cmd",
    "stop_notebook_cmd",
]
