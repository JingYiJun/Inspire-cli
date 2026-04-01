"""HPC subcommands other than create."""

from __future__ import annotations

import os
import sys
import time

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    EXIT_JOB_NOT_FOUND,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.cli.utils.auth import AuthManager, AuthenticationError
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.hpc_cache import HPCJobCache
from inspire.cli.utils.notebook_cli import get_base_url, require_web_session, resolve_json_output
from inspire.config import Config, ConfigError
from inspire.config.workspaces import select_workspace_id
from inspire.platform.web import browser_api as browser_api_module


def _load_config(*, require_credentials: bool) -> Config:
    config, _ = Config.from_files_and_env(require_credentials=require_credentials)
    return config


def _cache_path(config: Config) -> str:
    env_path = os.getenv("INSPIRE_HPC_JOB_CACHE")
    if env_path:
        return env_path
    return str((os.path.expanduser(config.job_cache_path)).replace("jobs.json", "hpc_jobs.json"))


def _cache(config: Config) -> HPCJobCache:
    return HPCJobCache(_cache_path(config))


def _unique_workspace_ids(values: list[str | None]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        ws_id = str(value or "").strip()
        if not ws_id:
            continue
        if ws_id in seen:
            continue
        seen.add(ws_id)
        unique.append(ws_id)
    return unique


def _resolve_hpc_list_workspace_ids(
    config: Config,
    *,
    workspace: str | None,
    workspace_id: str | None,
    all_workspaces: bool,
) -> list[str]:
    if workspace_id:
        return [workspace_id]
    if workspace:
        return [select_workspace_id(config, explicit_workspace_name=workspace)]
    if all_workspaces:
        return _unique_workspace_ids([config.workspace_cpu_id, config.workspace_hpc_id])
    return [select_workspace_id(config, explicit_workspace_name="hpc")]


def _workspace_alias(config: Config, workspace_id: str) -> str:
    if workspace_id == config.workspace_hpc_id:
        return "hpc"
    if workspace_id == config.workspace_cpu_id:
        return "cpu"
    for alias, candidate in (config.workspaces or {}).items():
        if workspace_id == candidate:
            return alias
    return workspace_id


def _hpc_job_to_dict(job: object, *, base_url: str, config: Config) -> dict[str, object]:
    job_id = str(getattr(job, "job_id", ""))
    path = f"/jobs/hpcDetail/{job_id}" if job_id else ""
    return {
        "job_id": job_id,
        "name": str(getattr(job, "name", "") or "N/A"),
        "status": str(getattr(job, "status", "") or "UNKNOWN"),
        "created_at": str(getattr(job, "created_at", "") or "N/A"),
        "created_by_id": str(getattr(job, "created_by_id", "") or ""),
        "created_by_name": str(getattr(job, "created_by_name", "") or ""),
        "project_id": str(getattr(job, "project_id", "") or ""),
        "project_name": str(getattr(job, "project_name", "") or ""),
        "compute_group_name": str(getattr(job, "compute_group_name", "") or ""),
        "workspace_id": str(getattr(job, "workspace_id", "") or ""),
        "workspace": _workspace_alias(config, str(getattr(job, "workspace_id", "") or "")),
        "path": path,
        "url": f"{base_url}{path}" if path else "",
    }


def _sort_hpc_job_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _normalize_status_filters(statuses: tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in statuses:
        status = str(value or "").strip().upper()
        if not status or status in seen:
            continue
        seen.add(status)
        normalized.append(status)
    return normalized


def _filter_jobs_by_status(
    jobs: list[dict[str, object]],
    *,
    statuses: list[str],
    limit: int,
) -> list[dict[str, object]]:
    if statuses:
        allowed = set(statuses)
        jobs = [job for job in jobs if str(job.get("status", "")).upper() in allowed]
    jobs = _sort_hpc_job_items(jobs)
    if limit > 0:
        return jobs[:limit]
    return jobs


@click.command("list")
@click.option(
    "--workspace", default=None, help="Workspace alias for remote listing (e.g. cpu, hpc)"
)
@click.option("--workspace-id", default=None, help="Workspace ID override")
@click.option(
    "--all",
    "all_workspaces",
    is_flag=True,
    help="List current-account jobs from both CPU + HPC workspaces",
)
@click.option(
    "--cache", "use_cache", is_flag=True, help="Use local cache only instead of the remote web list"
)
@click.option("--limit", "-n", type=int, default=100, help="Max jobs to show per workspace")
@click.option(
    "--status",
    "-s",
    multiple=True,
    help="Filter by status (e.g. RUNNING, STOPPED). Repeatable.",
)
@click.option("--json", "json_output", is_flag=True, help="Alias for global --json")
@pass_context
def list_jobs(
    ctx: Context,
    workspace: str | None,
    workspace_id: str | None,
    all_workspaces: bool,
    use_cache: bool,
    limit: int,
    status: tuple[str, ...],
    json_output: bool,
) -> None:
    """List HPC jobs created by the current account.

    By default this queries the remote web UI list for the configured HPC workspace.
    Use ``--all`` to combine CPU + HPC workspaces, or ``--cache`` to view the
    local submission cache only.

    \b
    Examples:
        inspire hpc list
        inspire hpc list --all
        inspire hpc list --workspace cpu -n 20
        inspire hpc list -s RUNNING
        inspire hpc list -s RUNNING -s STOPPED
        inspire hpc list --cache
        inspire hpc list --json
    """
    try:
        json_output = resolve_json_output(ctx, json_output)
        config = _load_config(require_credentials=False)
        status_filters = _normalize_status_filters(status)
        if use_cache:
            cache_limit = 0 if status_filters else limit
            jobs = _cache(config).list_jobs(limit=cache_limit)
            jobs = _filter_jobs_by_status(jobs, statuses=status_filters, limit=limit)
            if ctx.json_output:
                click.echo(json_formatter.format_json(jobs))
                return
            click.echo(human_formatter.format_job_list(jobs))
            return

        session = require_web_session(
            ctx,
            hint=(
                "Listing HPC jobs requires web authentication. "
                "Set [auth].username/password in config.toml or "
                "INSPIRE_USERNAME/INSPIRE_PASSWORD."
            ),
        )
        workspace_ids = _resolve_hpc_list_workspace_ids(
            config,
            workspace=workspace,
            workspace_id=workspace_id,
            all_workspaces=all_workspaces,
        )
        user = browser_api_module.get_current_user(session=session)
        created_by = str(user.get("id", "")).strip()
        base_url = get_base_url().rstrip("/")

        all_items: list[dict[str, object]] = []
        errors: list[tuple[str, str]] = []
        for ws_id in workspace_ids:
            try:
                workspace_items: list[dict[str, object]] = []
                seen_job_ids: set[str] = set()
                statuses_to_query = status_filters or [None]
                for status_value in statuses_to_query:
                    jobs, _ = browser_api_module.list_hpc_jobs(
                        workspace_id=ws_id,
                        created_by=created_by or None,
                        status=status_value,
                        page_num=1,
                        page_size=limit,
                        session=session,
                    )
                    for job in jobs:
                        item = _hpc_job_to_dict(job, base_url=base_url, config=config)
                        job_id = str(item.get("job_id", "")).strip()
                        if job_id and job_id in seen_job_ids:
                            continue
                        if job_id:
                            seen_job_ids.add(job_id)
                        workspace_items.append(item)

                workspace_items = _filter_jobs_by_status(
                    workspace_items,
                    statuses=status_filters,
                    limit=limit,
                )
                all_items.extend(workspace_items)
            except Exception as exc:  # noqa: BLE001
                errors.append((ws_id, str(exc)))

        if not all_items and errors:
            _handle_error(
                ctx,
                "APIError",
                "Failed to list HPC jobs from configured workspaces.",
                EXIT_API_ERROR,
            )
            return

        if errors and not ctx.json_output:
            for ws_id, message in errors:
                click.echo(f"Warning: workspace {ws_id} failed: {message}", err=True)

        all_items = _sort_hpc_job_items(all_items)
        if ctx.json_output:
            click.echo(json_formatter.format_json({"items": all_items, "total": len(all_items)}))
            return
        human_formatter.print_hpc_job_list(all_items)
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)


@click.command("status")
@click.argument("job_id")
@pass_context
def status(ctx: Context, job_id: str) -> None:
    """Show HPC job status."""
    try:
        config = _load_config(require_credentials=True)
        api = AuthManager.get_api(config)
        result = api.get_hpc_job_detail(job_id)
        data = result.get("data", {})
        _cache(config).upsert_job(job_id=job_id, data=data)
        if ctx.json_output:
            click.echo(json_formatter.format_json(data))
            return
        click.echo(human_formatter.format_hpc_status(data))
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)
    except AuthenticationError as exc:
        _handle_error(ctx, "AuthenticationError", str(exc), EXIT_AUTH_ERROR)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        code = (
            EXIT_JOB_NOT_FOUND if "job id" in message or "not found" in message else EXIT_API_ERROR
        )
        _handle_error(ctx, "APIError", str(exc), code)


@click.command("stop")
@click.argument("job_id")
@pass_context
def stop(ctx: Context, job_id: str) -> None:
    """Stop an HPC job."""
    try:
        config = _load_config(require_credentials=True)
        api = AuthManager.get_api(config)
        api.stop_hpc_job(job_id)
        _cache(config).update_status(job_id, "CANCELLED")
        if ctx.json_output:
            click.echo(json_formatter.format_json({"job_id": job_id, "status": "stopped"}))
            return
        click.echo(human_formatter.format_success(f"HPC job stopped: {job_id}"))
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)
    except AuthenticationError as exc:
        _handle_error(ctx, "AuthenticationError", str(exc), EXIT_AUTH_ERROR)
    except Exception as exc:  # noqa: BLE001
        _handle_error(ctx, "APIError", str(exc), EXIT_API_ERROR)


@click.command("wait")
@click.argument("job_id")
@click.option("--timeout", type=float, default=14400, help="Timeout in seconds")
@click.option("--interval", type=float, default=30, help="Poll interval in seconds")
@pass_context
def wait(ctx: Context, job_id: str, timeout: float, interval: float) -> None:
    """Wait for an HPC job to reach a terminal state."""
    try:
        config = _load_config(require_credentials=True)
        api = AuthManager.get_api(config)
        cache = _cache(config)
        start = time.time()
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "STOPPED"}
        successful_terminal = {"SUCCEEDED", "CANCELLED", "STOPPED"}

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                _handle_error(ctx, "Timeout", f"Timeout after {timeout}s", EXIT_TIMEOUT)
                return

            result = api.get_hpc_job_detail(job_id)
            data = result.get("data", {})
            cache.upsert_job(job_id=job_id, data=data)
            status_value = str(data.get("status", "UNKNOWN"))
            if status_value in terminal:
                if ctx.json_output:
                    click.echo(json_formatter.format_json(data))
                else:
                    click.echo(human_formatter.format_hpc_status(data))
                if status_value in successful_terminal:
                    sys.exit(EXIT_SUCCESS)
                sys.exit(EXIT_GENERAL_ERROR)

            if interval > 0:
                time.sleep(interval)
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)
    except AuthenticationError as exc:
        _handle_error(ctx, "AuthenticationError", str(exc), EXIT_AUTH_ERROR)


@click.command("script")
@click.argument("job_id")
@pass_context
def script(ctx: Context, job_id: str) -> None:
    """Show cached sbatch script for an HPC job."""
    try:
        config = _load_config(require_credentials=False)
        job = _cache(config).get_job(job_id)
        if not job or not job.get("entrypoint"):
            _handle_error(
                ctx, "ScriptNotFound", f"No cached script found for {job_id}", EXIT_JOB_NOT_FOUND
            )
            return
        if ctx.json_output:
            click.echo(
                json_formatter.format_json({"job_id": job_id, "entrypoint": job["entrypoint"]})
            )
            return
        click.echo(job["entrypoint"])
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)


__all__ = ["list_jobs", "script", "status", "stop", "wait"]
