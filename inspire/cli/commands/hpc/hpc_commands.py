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
from inspire.config import Config, ConfigError


def _cache_path(config: Config) -> str:
    env_path = os.getenv("INSPIRE_HPC_JOB_CACHE")
    if env_path:
        return env_path
    return str((os.path.expanduser(config.job_cache_path)).replace("jobs.json", "hpc_jobs.json"))


def _cache(config: Config) -> HPCJobCache:
    return HPCJobCache(_cache_path(config))


@click.command("list")
@click.option("--limit", "-n", type=int, default=10, help="Max jobs to show")
@pass_context
def list_jobs(ctx: Context, limit: int) -> None:
    """List cached HPC jobs."""
    try:
        config = Config.from_env()
        jobs = _cache(config).list_jobs(limit=limit)
        if ctx.json_output:
            click.echo(json_formatter.format_json(jobs))
            return
        click.echo(human_formatter.format_job_list(jobs))
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)


@click.command("status")
@click.argument("job_id")
@pass_context
def status(ctx: Context, job_id: str) -> None:
    """Show HPC job status."""
    try:
        config = Config.from_env()
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
        config = Config.from_env()
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
        config = Config.from_env()
        api = AuthManager.get_api(config)
        cache = _cache(config)
        start = time.time()
        terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "STOPPED"}

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
                if status_value == "SUCCEEDED":
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
        config = Config.from_env()
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
