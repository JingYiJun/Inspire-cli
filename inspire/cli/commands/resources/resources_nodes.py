"""Resources nodes command (full free nodes per group)."""

from __future__ import annotations

import shutil

import click

from inspire.cli.context import Context, EXIT_API_ERROR, EXIT_AUTH_ERROR, pass_context
from inspire.cli.formatters import json_formatter
from inspire.platform.web import browser_api as browser_api_module
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.platform.web.session import SessionExpiredError

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


@click.command("nodes")
@click.option("--group", help="Filter by compute group name (partial match)")
@pass_context
def list_nodes(ctx: Context, group: str) -> None:
    """Show how many FULL 8-GPU nodes are currently free per compute group.

    This uses the browser-only endpoint POST /api/v1/cluster_nodes/list
    (filtered by logic_compute_group_id), so it accounts for GPU fragmentation
    across nodes.

    \b
    Examples:
        inspire resources nodes
        inspire resources nodes --group H200
    """
    try:
        # Fetch compute groups dynamically from the API
        accurate_availability = browser_api_module.get_accurate_gpu_availability()
        accurate_map = {a.group_id: a.available_gpus for a in accurate_availability}
        name_map = {a.group_id: a.group_name for a in accurate_availability}

        group_ids = [a.group_id for a in accurate_availability]
        counts = browser_api_module.get_full_free_node_counts(group_ids, gpu_per_node=8)

        # Fill missing names and apply filter
        filtered: list[dict] = []
        group_lower = (group or "").lower()
        for c in counts:
            name = c.group_name or name_map.get(c.group_id, c.group_id[-12:])
            if group_lower and group_lower not in name.lower():
                continue
            # Use accurate available GPUs if available, otherwise fall back to computed
            free_gpus = accurate_map.get(c.group_id, c.full_free_nodes * c.gpu_per_node)
            filtered.append(
                {
                    "group_id": c.group_id,
                    "group_name": name,
                    "gpu_per_node": c.gpu_per_node,
                    "total_nodes": c.total_nodes,
                    "ready_nodes": c.ready_nodes,
                    "full_free_nodes": c.full_free_nodes,
                    "full_free_gpus": free_gpus,
                }
            )

        # Sort by full_free_nodes descending
        filtered.sort(key=lambda x: x["full_free_nodes"], reverse=True)

        if ctx.json_output:
            click.echo(
                json_formatter.format_json(
                    {
                        "groups": filtered,
                        "total_full_free_nodes": sum(x["full_free_nodes"] for x in filtered),
                    }
                )
            )
            return

        total_full_free = 0
        total_free_gpus = 0
        rows: list[tuple[str, int, int, int, int, str]] = []
        for row in filtered:
            name = row["group_name"][:24]
            full_free = row["full_free_nodes"]
            ready = row["ready_nodes"]
            total = row["total_nodes"]
            free_gpus = row["full_free_gpus"]

            total_full_free += full_free
            total_free_gpus += free_gpus

            if full_free >= 10:
                indicator = "🟢"
            elif full_free >= 3:
                indicator = "🟡"
            elif full_free > 0:
                indicator = "🟠"
            else:
                indicator = "🔴"

            rows.append((name, full_free, ready, total, free_gpus, indicator))

        console = _make_console()
        if console is None or Table is None or box is None:
            click.echo("")
            click.echo("📊 Full-Free 8-GPU Nodes by Compute Group")
            click.echo("─" * 78)
            click.echo(
                f"{'Group':<25} {'Full Free':>10} {'Ready':>8} {'Total':>8} {'Free GPUs':>10}"
            )
            click.echo("─" * 78)
            for name, full_free, ready, total, free_gpus, indicator in rows:
                click.echo(
                    f"{name:<25} {full_free:>10} {ready:>8} {total:>8} {free_gpus:>10} {indicator}"
                )
            click.echo("─" * 78)
            click.echo(f"{'TOTAL':<25} {total_full_free:>10} {'':>8} {'':>8} {total_free_gpus:>10}")
            click.echo("")
            click.echo("Full Free = READY nodes with 8 GPUs and no running tasks")
            click.echo("Free GPUs = Total available GPUs (matches 'inspire resources list')")
            click.echo("")
        else:
            table = Table(
                title="Full-Free 8-GPU Nodes by Compute Group",
                box=box.SIMPLE_HEAVY,
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Group", style="cyan")
            table.add_column("Full Free", justify="right")
            table.add_column("Ready", justify="right")
            table.add_column("Total", justify="right")
            table.add_column("Free GPUs", justify="right")
            table.add_column("", justify="center")
            for name, full_free, ready, total, free_gpus, indicator in rows:
                table.add_row(
                    name,
                    str(full_free),
                    str(ready),
                    str(total),
                    str(free_gpus),
                    indicator,
                )
            table.add_section()
            table.add_row("TOTAL", str(total_full_free), "", "", str(total_free_gpus), "")
            console.print(table)
            console.print("Full Free = READY nodes with 8 GPUs and no running tasks")
            console.print("Free GPUs = Total available GPUs (matches 'inspire resources list')")

    except (SessionExpiredError, ValueError) as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
