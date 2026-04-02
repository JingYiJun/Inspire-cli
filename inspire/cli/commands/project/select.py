"""Project select command – interactive project priority selector."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
import questionary
import tomlkit

from inspire.cli.context import (
    Context,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    pass_context,
)
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.output import emit_info, emit_success
from inspire.config import Config, ConfigError, PROJECT_CONFIG_DIR
from inspire.config.toml import _find_project_config


def _build_project_indexes(
    available_projects: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, int]]:
    """Build lookup tables for project IDs and unique names."""
    projects_by_id = {str(p.get("id") or ""): p for p in available_projects}
    name_counts: dict[str, int] = {}
    for p in available_projects:
        norm = str(p.get("name") or "").strip().casefold()
        if norm:
            name_counts[norm] = name_counts.get(norm, 0) + 1

    projects_by_unique_name = {
        (norm := str(p.get("name") or "").strip().casefold()): p
        for p in available_projects
        if norm and name_counts.get(norm) == 1
    }
    return projects_by_id, projects_by_unique_name, name_counts


def _resolve_project_entry(
    entry: str,
    *,
    projects_by_id: dict[str, dict[str, Any]],
    projects_by_unique_name: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a project_order entry by ID first, then by unique name."""
    entry = str(entry or "").strip()
    if not entry:
        return None
    if entry in projects_by_id:
        return projects_by_id[entry]
    return projects_by_unique_name.get(entry.casefold())


def _serialize_project_order(
    ordered_project_ids: list[str],
    *,
    projects_by_id: dict[str, dict[str, Any]],
    name_counts: dict[str, int],
) -> list[str]:
    """Serialize project order, preferring unique names and falling back to IDs."""
    serialized: list[str] = []
    for pid in ordered_project_ids:
        project = projects_by_id.get(pid)
        if project is None:
            serialized.append(pid)
            continue
        name = str(project.get("name") or "").strip()
        norm = name.casefold()
        if name and name_counts.get(norm) == 1:
            serialized.append(name)
        else:
            serialized.append(pid)
    return serialized


def _select_and_order_projects(
    available_projects: list[dict[str, Any]],
    current_order: list[str],
    *,
    projects_by_id: dict[str, dict[str, Any]],
    projects_by_unique_name: dict[str, dict[str, Any]],
    name_counts: dict[str, int],
) -> list[str] | None:
    """Interactive project selection and ordering.

    Returns list of project IDs in priority order, or None if cancelled.
    """
    checked_ids = {
        str(r.get("id") or "")
        for entry in current_order
        if (
            r := _resolve_project_entry(
                entry,
                projects_by_id=projects_by_id,
                projects_by_unique_name=projects_by_unique_name,
            )
        )
    }

    choices = [
        questionary.Choice(
            title=str(p.get("name") or p.get("id") or "Unknown"),
            value=str(p.get("id") or ""),
            checked=str(p.get("id") or "") in checked_ids,
        )
        for p in available_projects
    ]

    selected = questionary.checkbox(
        "Select projects to include in priority order:\n"
        "(Use Space to select/deselect, Enter to confirm)",
        choices=choices,
    ).ask()

    if selected is None:
        return None
    if not selected:
        return []
    if len(selected) == 1:
        return _serialize_project_order(
            selected, projects_by_id=projects_by_id, name_counts=name_counts
        )

    click.echo("\nNow let's set the priority order (highest priority first):\n")

    ordered: list[str] = []
    remaining = selected.copy()

    for i in range(len(selected)):
        if len(remaining) == 1:
            ordered.append(remaining[0])
            break

        remaining_choices = [c for c in choices if c.value in remaining]
        choice = questionary.select(
            f"Priority {i + 1} (highest):",
            choices=remaining_choices,
        ).ask()

        if choice is None:
            return None
        ordered.append(choice)
        remaining.remove(choice)

    return _serialize_project_order(ordered, projects_by_id=projects_by_id, name_counts=name_counts)


def _update_project_config(project_path: Path, project_order: list[str]) -> None:
    """Update the project config with new project_order."""
    try:
        if project_path.exists():
            doc = tomlkit.parse(project_path.read_text(encoding="utf-8"))
        else:
            doc = tomlkit.document()
            doc.add(tomlkit.comment("Inspire CLI Project Configuration"))
            doc.add(tomlkit.nl())

        if "defaults" not in doc:
            doc["defaults"] = tomlkit.table()

        if project_order:
            doc["defaults"]["project_order"] = project_order
        elif "project_order" in doc["defaults"]:
            del doc["defaults"]["project_order"]

        project_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    except Exception as e:
        raise ConfigError(f"Failed to update project configuration: {e}") from e


@click.command("select")
@click.option(
    "--reset",
    is_flag=True,
    help="Clear project_order (no projects preferred)",
)
@click.option(
    "--json",
    "json_output_local",
    is_flag=True,
    help="Show current project_order as JSON",
)
@pass_context
def select_projects(
    ctx: Context,
    reset: bool,
    json_output_local: bool,
) -> None:
    """Interactively select and prioritize projects.

    Launches an interactive picker to select which projects to use and
    set their priority order. The order is saved to defaults.project_order
    in the project config.

    Projects are used in priority order when --project is not specified
    on commands like 'job create'.

    \b
    Examples:
        inspire project select              # Interactive selection
        inspire project select --reset      # Clear project_order
        inspire project select --json       # Show current order as JSON
    """
    ctx.json_output = bool(ctx.json_output or json_output_local)
    effective_json = ctx.json_output

    try:
        config, _ = Config.from_files_and_env(require_credentials=False)

        project_path = _find_project_config()
        if not project_path:
            project_path = Path.cwd() / PROJECT_CONFIG_DIR / "config.toml"

        if reset:
            if effective_json:
                click.echo(json.dumps({"project_order": [], "action": "reset"}))
            else:
                _update_project_config(project_path, [])
                emit_success(ctx, text="Project order cleared", payload={"project_order": []})
            return

        if effective_json:
            current_order = config.project_order or []
            click.echo(
                json.dumps(
                    {
                        "project_order": current_order,
                        "config_path": str(project_path),
                    }
                )
            )
            return

        # Build available projects list from catalog + workdirs
        projects: list[dict[str, Any]] = []
        if config.project_catalog:
            for pid, meta in config.project_catalog.items():
                projects.append({"id": pid, **meta})
        for pid, workdir in config.project_workdirs.items():
            if not any(p["id"] == pid for p in projects):
                catalog = config.project_catalog.get(pid) if config.project_catalog else None
                projects.append(
                    {
                        "id": pid,
                        "name": (catalog.get("name") if catalog else "") or "",
                        "workdir": workdir,
                    }
                )

        if not projects:
            _handle_error(
                ctx,
                "ConfigError",
                "No projects found in the discovered project catalog.",
                EXIT_CONFIG_ERROR,
                hint="Run 'inspire init --discover' to discover projects first.",
            )
            return

        all_have_names = all(
            str(p.get("name", "")).strip() and not str(p.get("name", "")).startswith("project-")
            for p in projects
        )
        if not all_have_names:
            click.echo(
                click.style(
                    "Note: Project names not available. Run 'inspire init --discover' to fetch project names.",
                    fg="yellow",
                )
            )
            click.echo()

        projects_by_id, projects_by_unique_name, name_counts = _build_project_indexes(projects)

        current_order = config.project_order or []
        if current_order:
            click.echo(click.style("\nCurrent project priority order:", fg="blue"))
            for i, entry in enumerate(current_order, 1):
                resolved = _resolve_project_entry(
                    entry,
                    projects_by_id=projects_by_id,
                    projects_by_unique_name=projects_by_unique_name,
                )
                label = (
                    str(resolved.get("name") or resolved.get("id") or entry) if resolved else entry
                )
                click.echo(f"  {i}. {label}")
            click.echo()
        else:
            click.echo(click.style("\nNo project priority order configured.", fg="yellow"))
            click.echo("Select projects below to set the order.\n")

        new_order = _select_and_order_projects(
            projects,
            current_order,
            projects_by_id=projects_by_id,
            projects_by_unique_name=projects_by_unique_name,
            name_counts=name_counts,
        )

        if new_order is None:
            emit_info(ctx, "Selection cancelled.")
            return

        _update_project_config(project_path, new_order)
        click.echo()
        emit_success(
            ctx,
            text="Project order updated successfully",
            payload={"project_order": new_order},
        )

        click.echo()
        click.echo(click.style("New project priority order:", fg="green"))
        for i, entry in enumerate(new_order, 1):
            resolved = _resolve_project_entry(
                entry,
                projects_by_id=projects_by_id,
                projects_by_unique_name=projects_by_unique_name,
            )
            label = str(resolved.get("name") or resolved.get("id") or entry) if resolved else entry
            click.echo(f"  {i}. {label}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)
