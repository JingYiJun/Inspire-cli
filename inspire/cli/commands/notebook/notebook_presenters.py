"""Presentation helpers for notebook CLI output."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import click

from inspire.cli.formatters import json_formatter
from .notebook_lookup import _extract_notebook_resource_fields, _format_notebook_resource

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - optional dependency fallback
    box = None
    Console = None
    Table = None


COLUMN_SPECS = {
    "name": ("Name", 25),
    "status": ("Status", 12),
    "resource": ("Resource", 20),
    "id": ("ID", 38),
    "created": ("Created", 20),
    "gpu": ("GPU", 10),
    "cpu": ("CPU", 10),
    "memory": ("Memory", 10),
    "image": ("Image", 25),
    "project": ("Project", 20),
    "workspace": ("Workspace", 15),
    "node": ("Node", 15),
    "uptime": ("Uptime", 10),
    "tunnel": ("Tunnel", 15),
}


def _make_console() -> Console | None:
    if Console is None:
        return None
    terminal_width = shutil.get_terminal_size((120, 24)).columns
    return Console(width=max(100, terminal_width))


def _format_notebook_resource_summary(notebook: dict) -> str:
    cpu_display, gpu_display, memory_display = _extract_notebook_resource_fields(notebook)
    parts = [value.strip() for value in (cpu_display, gpu_display, memory_display) if value.strip()]
    return " · ".join(parts) if parts else "N/A"


def _normalize_notebook_id(notebook_id: str) -> str:
    if notebook_id.startswith("notebook-"):
        return notebook_id[len("notebook-") :]
    return notebook_id


def _get_tunnel_name(notebook: dict, tunnel_config) -> str:
    if not tunnel_config:
        return "-"
    notebook_id = str(notebook.get("notebook_id") or notebook.get("id") or "")
    normalized_id = _normalize_notebook_id(notebook_id)
    for bridge in tunnel_config.list_bridges():
        bridge_id = str(getattr(bridge, "notebook_id", "") or "")
        if bridge_id and _normalize_notebook_id(bridge_id) == normalized_id:
            return str(getattr(bridge, "name", "-") or "-")
    return "-"


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
    raw_value = created_at
    raw = str(created_at or "").strip()
    if not raw:
        return None

    if isinstance(raw_value, (int, float)) or raw.isdigit():
        try:
            timestamp = float(raw)
            if timestamp >= 1_000_000_000_000:
                timestamp /= 1000.0
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            human = dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S UTC+8")
            return f"{human} ({raw})"
        except (OverflowError, OSError, ValueError):
            return raw

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


def _format_column(item: dict, col: str, tunnel_config=None) -> str:
    image = item.get("image") or {}
    workspace = item.get("workspace") or {}
    extra = item.get("extra_info") or {}
    cpu_display, gpu_display, memory_display = _extract_notebook_resource_fields(item)

    if col == "name":
        return str(item.get("name", "N/A"))
    if col == "status":
        return str(item.get("status", "Unknown"))
    if col == "resource":
        return _format_notebook_resource(item)
    if col == "id":
        return str(item.get("notebook_id") or item.get("id", "N/A"))
    if col == "created":
        return _format_notebook_created_at(item.get("created_at")) or "N/A"
    if col == "gpu":
        return gpu_display
    if col == "cpu":
        return cpu_display
    if col == "memory":
        return memory_display
    if col == "image":
        name = image.get("name", "")
        version = image.get("version", "")
        return f"{name}:{version}" if name and version else str(name or "N/A")
    if col == "project":
        return str(item.get("project", {}).get("name") or item.get("project_name") or "N/A")
    if col == "workspace":
        return str(workspace.get("name") or "N/A")
    if col == "node":
        return str(extra.get("NodeName") or "N/A")
    if col == "uptime":
        return _format_notebook_uptime(item.get("live_time")) or "N/A"
    if col == "tunnel":
        return _get_tunnel_name(item, tunnel_config)
    return "N/A"


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


def _print_notebook_list(
    items: list,
    json_output: bool,
    columns: str = "name,status,resource,id",
    tunnel_config=None,
) -> None:
    """Print notebook list in appropriate format."""
    if json_output:
        click.echo(json_formatter.format_json({"items": items, "total": len(items)}))
        return

    if not items:
        click.echo("No notebook instances found.")
        return

    column_list = [col.strip().lower() for col in columns.split(",") if col.strip()]
    if not column_list:
        column_list = ["name", "status", "resource", "id"]

    valid_columns = [col for col in column_list if col in COLUMN_SPECS]
    if not valid_columns:
        valid_columns = ["name", "status", "resource", "id"]

    if valid_columns in (
        ["name", "status", "resource"],
        ["name", "status", "resource", "id"],
    ):
        valid_columns = ["name", "status", "cpu", "gpu", "memory", "id"]

    console = _make_console()
    if console is None or Table is None or box is None:
        widths = {
            col: max(
                COLUMN_SPECS[col][1],
                max(len(_format_column(item, col, tunnel_config)) for item in items),
            )
            for col in valid_columns
        }
        header = " ".join(f"{COLUMN_SPECS[col][0]:<{widths[col]}}" for col in valid_columns)
        lines = [header, "-" * len(header)]
        for item in items:
            lines.append(
                " ".join(
                    f"{_format_column(item, col, tunnel_config)[:widths[col]]:<{widths[col]}}"
                    for col in valid_columns
                )
            )
        lines.append(f"\nShowing {len(items)} notebook(s)")
        click.echo("\n".join(lines))
        return

    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
    )
    for col in valid_columns:
        header, _width = COLUMN_SPECS[col]
        justify = "right" if col in {"cpu", "gpu", "memory"} else "left"
        style = "green" if col == "status" else ("cyan" if col == "name" else "white")
        table.add_column(header, style=style, justify=justify, no_wrap=(col == "id"))
    for item in items:
        table.add_row(*[_format_column(item, col, tunnel_config) for col in valid_columns])
    console.print(table)
    console.print(f"Showing {len(items)} notebook(s)")


__all__ = ["_print_notebook_detail", "_print_notebook_list", "COLUMN_SPECS"]
