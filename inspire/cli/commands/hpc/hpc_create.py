"""HPC create command."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_VALIDATION_ERROR,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.cli.utils.auth import AuthManager, AuthenticationError
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.hpc_submit import (
    build_hpc_create_payload,
    cache_created_hpc_job,
    merge_hpc_overrides,
    resolve_hpc_preset,
)
from inspire.cli.utils.hpc_script import extract_hpc_execution_body, validate_hpc_script
from inspire.config import Config, ConfigError
from inspire.config.workspaces import select_workspace_id


def _resolve_project_id(config: Config, explicit_project_id: Optional[str]) -> str:
    project_id = explicit_project_id or config.job_project_id
    if not project_id:
        raise ValueError("No project configured. Use --project-id or configure job.project_id.")
    return project_id


def _load_script_file(script_file: Optional[str]) -> str | None:
    if not script_file:
        return None
    content = Path(script_file).read_text(encoding="utf-8")
    validate_hpc_script(content)
    return extract_hpc_execution_body(content)


@click.command("create")
@click.option("--name", "-n", required=True, help="HPC job name")
@click.option("--preset", default=None, help="HPC preset name")
@click.option("--command", default=None, help="Business command to run via srun")
@click.option("--script-file", default=None, help="Read full sbatch script from file")
@click.option("--workspace", default=None, help="Workspace alias (e.g. cpu, hpc)")
@click.option("--workspace-id", default=None, help="Explicit workspace id override")
@click.option("--project-id", default=None, help="Explicit project id override")
@click.option("--image", default=None, help="Override image")
@click.option("--image-type", default=None, help="Override image type")
@click.option("--number-of-tasks", type=int, default=None, help="Override ntasks")
@click.option("--cpus-per-task", type=int, default=None, help="Override cpus-per-task")
@click.option("--memory-per-cpu", default=None, help="Override memory per cpu (e.g. 4G)")
@click.option("--time-limit", default=None, help="Override time limit (day-hour:min:sec)")
@click.option(
    "--enable-hyper-threading/--disable-hyper-threading",
    default=None,
    help="Override hyper-threading",
)
@pass_context
def create(
    ctx: Context,
    name: str,
    preset: Optional[str],
    command: Optional[str],
    script_file: Optional[str],
    workspace: Optional[str],
    workspace_id: Optional[str],
    project_id: Optional[str],
    image: Optional[str],
    image_type: Optional[str],
    number_of_tasks: Optional[int],
    cpus_per_task: Optional[int],
    memory_per_cpu: Optional[str],
    time_limit: Optional[str],
    enable_hyper_threading: Optional[bool],
) -> None:
    """Create a new HPC job."""
    if command and script_file:
        raise click.UsageError("Cannot use --command with --script-file")
    if not command and not script_file:
        raise click.UsageError("Either --command or --script-file is required")

    try:
        config, _ = Config.from_files_and_env(require_credentials=True)
        api = AuthManager.get_api(config)

        preset_data = resolve_hpc_preset(config, preset)
        resolved_workspace_name = (
            workspace or str(preset_data.get("workspace") or "").strip() or None
        )
        resolved_workspace_id = select_workspace_id(
            config,
            cpu_only=True,
            explicit_workspace_id=workspace_id,
            explicit_workspace_name=resolved_workspace_name,
        )
        if not resolved_workspace_id:
            _handle_error(
                ctx, "ConfigError", "No workspace configured for HPC jobs.", EXIT_CONFIG_ERROR
            )
            return

        merged = merge_hpc_overrides(
            preset_data,
            {
                "number_of_tasks": number_of_tasks,
                "cpus_per_task": cpus_per_task,
                "memory_per_cpu": memory_per_cpu,
                "time": time_limit,
                "enable_hyper_threading": enable_hyper_threading,
            },
        )
        resolved_project_id = _resolve_project_id(config, project_id)
        script_body = _load_script_file(script_file)
        payload = build_hpc_create_payload(
            config=config,
            name=name,
            project_id=resolved_project_id,
            workspace_id=resolved_workspace_id,
            image=image,
            image_type=image_type,
            merged_config=merged,
            command=command,
            script_body=script_body,
        )
        result = api.create_hpc_job(**payload)
        data = result.get("data", {})
        job_id = data.get("job_id")
        if job_id:
            cache_created_hpc_job(
                config=config,
                job_id=job_id,
                name=name,
                entrypoint=payload["entrypoint"],
                project=resolved_project_id,
                metadata={
                    "logic_compute_group_id": payload["logic_compute_group_id"],
                    "workspace_id": payload["workspace_id"],
                    "image": payload["image"],
                    "number_of_tasks": payload["number_of_tasks"],
                    "cpus_per_task": payload["cpus_per_task"],
                    "memory_per_cpu": payload["memory_per_cpu"],
                },
            )

        if ctx.json_output:
            click.echo(json_formatter.format_json(data or result))
            return
        click.echo(human_formatter.format_success(f"HPC job created: {job_id}"))
        click.echo(f"\nCheck status with: inspire hpc status {job_id}")
    except ConfigError as exc:
        _handle_error(ctx, "ConfigError", str(exc), EXIT_CONFIG_ERROR)
    except AuthenticationError as exc:
        _handle_error(ctx, "AuthenticationError", str(exc), EXIT_AUTH_ERROR)
    except ValueError as exc:
        _handle_error(ctx, "ValidationError", str(exc), EXIT_VALIDATION_ERROR)
    except Exception as exc:  # noqa: BLE001
        _handle_error(ctx, "APIError", str(exc), EXIT_API_ERROR)
