"""Presentation helpers for notebook CLI output."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import click

from inspire.cli.formatters import json_formatter
from .notebook_lookup import _extract_notebook_resource_fields

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - optional dependency fallback
    box = None
    Console = None
    Table = None


def _make_console() -> Console | None:
    if Console is None:
        return None
    terminal_width = shutil.get_terminal_size((120, 24)).columns
    return Console(width=max(100, terminal_width))


def _format_notebook_resource_summary(notebook: dict) -> str:
    cpu_display, gpu_display, memory_display = _extract_notebook_resource_fields(notebook)
    parts = [value.strip() for value in (cpu_display, gpu_display, memory_display) if value.strip()]
    return " · ".join(parts) if parts else "N/A"


def _format_notebook_uptime(live_seconds: object) -> str | None:
    try:
        seconds = int(live_seconds or 0)
    except (TypeError, ValueError):
        return None

    if seconds <= 0:
        return None

    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not hours:
        parts.append(f"{seconds}s")
    return " ".join(parts) or "< 1m"


def _notebook_status_style(status: object) -> str:
    normalized = str(status or "").strip().upper()
    if normalized in {"RUNNING", "READY"}:
        return "bold green"
    if normalized in {"QUEUING", "PENDING", "CREATING", "STARTING", "STARTED"}:
        return "bold yellow"
    if normalized in {"STOPPED", "STOPPING"}:
        return "bold bright_black"
    if normalized in {"FAILED", "ERROR", "DELETED"}:
        return "bold red"
    return "bold white"


def _format_notebook_created_at(created_at: object) -> str | None:
    raw = str(created_at or "").strip()
    if not raw:
        return None

    try:
        normalized = raw.replace("Z", "+00:00")
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        return raw

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    local_dt = timestamp.astimezone(timezone(timedelta(hours=8)))
    human = local_dt.strftime("%Y-%m-%d %H:%M:%S UTC+8")
    return f"{human} ({raw})"


def _print_notebook_detail(notebook: dict) -> None:
    """Print detailed notebook information."""
    project = notebook.get("project") or {}
    quota = notebook.get("quota") or {}
    compute_group = notebook.get("logic_compute_group") or {}
    extra = notebook.get("extra_info") or {}
    image = notebook.get("image") or {}
    start_cfg = notebook.get("start_config") or {}
    workspace = notebook.get("workspace") or {}
    node = notebook.get("node") or {}

    gpu_type = ""
    node_gpu_info = node.get("gpu_info")
    if isinstance(node_gpu_info, dict):
        gpu_type = node_gpu_info.get("gpu_product_simple", "")
    if not gpu_type:
        spec = notebook.get("resource_spec") or {}
        gpu_type = spec.get("gpu_type", "")

    img_name = image.get("name", "")
    img_ver = image.get("version", "")
    img_str = f"{img_name}:{img_ver}" if img_name and img_ver else img_name or "N/A"
    shm = start_cfg.get("shared_memory_size", 0) or 0
    uptime = _format_notebook_uptime(notebook.get("live_time"))
    resource_summary = _format_notebook_resource_summary(notebook)
    notebook_name = str(notebook.get("name", "N/A"))
    notebook_id = str(notebook.get("notebook_id") or notebook.get("id") or "N/A")
    status = str(notebook.get("status") or "Unknown")

    fields = [
        ("Name", notebook_name),
        ("ID", notebook_id),
        ("Status", status),
        ("Resource", resource_summary),
        ("Project", project.get("name") or notebook.get("project_name")),
        ("Priority", project.get("priority_name")),
        ("Compute Group", compute_group.get("name")),
        ("Image", img_str),
        ("CPU", quota.get("cpu_count")),
        ("Memory", f"{quota['memory_size']} GiB" if quota.get("memory_size") else None),
        ("SHM", f"{shm} GiB" if shm else None),
        ("Node", extra.get("NodeName") or None),
        ("Host IP", extra.get("HostIP") or None),
        ("Uptime", uptime or None),
        ("Workspace", workspace.get("name")),
        ("Created", _format_notebook_created_at(notebook.get("created_at"))),
    ]

    console = _make_console()
    if console is not None and Table is not None and box is not None:
        table = Table(
            title=f"Notebook Status · {notebook_name}",
            box=box.SIMPLE_HEAVY,
            show_header=False,
            pad_edge=False,
        )
        table.add_column("Field", style="bold cyan", no_wrap=True, width=14)
        table.add_column("Value", style="white")

        for label, value in fields:
            if not value:
                continue
            row_style = _notebook_status_style(value) if label == "Status" else None
            table.add_row(label, str(value), style=row_style)

        console.print(table)
        return

    click.echo(f"\n{'='*60}")
    click.echo(f"Notebook: {notebook_name}")
    click.echo(f"{'='*60}")
    for label, value in fields:
        if value:
            click.echo(f"  {label:<15}: {value}")
    click.echo(f"{'='*60}\n")


def _print_notebook_list(items: list, json_output: bool) -> None:
    """Print notebook list in appropriate format."""
    if json_output:
        click.echo(json_formatter.format_json({"items": items, "total": len(items)}))
        return

    if not items:
        click.echo("No notebook instances found.")
        return

    resource_fields = [_extract_notebook_resource_fields(item) for item in items]
    console = _make_console()
    if console is None or Table is None or box is None:
        cpu_width = max(len("CPU"), max(len(cpu) for cpu, _, _ in resource_fields))
        gpu_width = max(len("GPU"), max(len(gpu) for _, gpu, _ in resource_fields))
        memory_width = max(len("Memory"), max(len(memory) for _, _, memory in resource_fields))
        lines = [
            f"{'Name':<25} {'Status':<12} {'CPU':>{cpu_width}} {'GPU':>{gpu_width}} "
            f"{'Memory':>{memory_width}} {'ID':<38}",
            "-" * (25 + 1 + 12 + 1 + cpu_width + 1 + gpu_width + 1 + memory_width + 1 + 38),
        ]
        for item, (cpu_display, gpu_display, memory_display) in zip(items, resource_fields):
            name = item.get("name", "N/A")[:25]
            status = item.get("status", "Unknown")[:12]
            notebook_id = item.get("notebook_id", item.get("id", "N/A"))
            lines.append(
                f"{name:<25} {status:<12} {cpu_display:>{cpu_width}} {gpu_display:>{gpu_width}} "
                f"{memory_display:>{memory_width}} {notebook_id:<38}"
            )
        lines.append(f"\nShowing {len(items)} notebook(s)")
        click.echo("\n".join(lines))
        return

    table = Table(
        title="Notebook Instances",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("CPU", justify="right")
    table.add_column("GPU", justify="right")
    table.add_column("Memory", justify="right")
    table.add_column("ID", style="white", no_wrap=True)
    for item, (cpu_display, gpu_display, memory_display) in zip(items, resource_fields):
        table.add_row(
            str(item.get("name", "N/A")),
            str(item.get("status", "Unknown")),
            cpu_display,
            gpu_display,
            memory_display,
            str(item.get("notebook_id", item.get("id", "N/A"))),
        )
    console.print(table)
    console.print(f"Showing {len(items)} notebook(s)")


__all__ = ["_print_notebook_detail", "_print_notebook_list"]
