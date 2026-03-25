"""Human-readable output formatter for CLI commands.

Provides compact plain-text output for terminal and agent use.
"""

from __future__ import annotations

import sys
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
except ImportError:  # pragma: no cover - optional dependency fallback
    box = None
    Console = None
    Table = None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def format_error(message: str, hint: Optional[str] = None) -> str:
    """Format an error message.

    Args:
        message: Error message
        hint: Optional hint for fixing

    Returns:
        Formatted error string
    """
    lines = [f"Error: {message}"]
    if hint:
        lines.append(f"Hint: {hint}")
    return "\n".join(lines)


def format_success(message: str) -> str:
    """Format a success message.

    Args:
        message: Success message

    Returns:
        Formatted success string
    """
    return f"OK {message}"


def format_warning(message: str) -> str:
    """Format a warning message.

    Args:
        message: Warning message

    Returns:
        Formatted warning string
    """
    return f"Warning: {message}"


def print_error(message: str, hint: Optional[str] = None) -> None:
    """Print an error message to stderr."""
    print(format_error(message, hint), file=sys.stderr)


def _make_console() -> Console | None:
    if Console is None:
        return None
    terminal_width = shutil.get_terminal_size((120, 24)).columns
    return Console(width=max(100, terminal_width))


def _render_plain_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    aligns: Optional[list[str]] = None,
) -> str:
    if not rows:
        return ""

    aligns = aligns or ["left"] * len(headers)
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    def _format_row(row: list[str]) -> str:
        formatted: list[str] = []
        for idx, cell in enumerate(row):
            text = str(cell)
            if aligns[idx] == "right":
                formatted.append(text.rjust(widths[idx]))
            else:
                formatted.append(text.ljust(widths[idx]))
        return " ".join(formatted)

    lines = [_format_row(headers), "-" * (sum(widths) + len(widths) - 1)]
    lines.extend(_format_row(row) for row in rows)
    return "\n".join(lines)


def _print_rich_table(
    *,
    title: str,
    headers: list[tuple[str, dict[str, Any]]],
    rows: list[list[Any]],
    footer: Optional[str] = None,
) -> bool:
    console = _make_console()
    if console is None or Table is None or box is None:
        return False

    table = Table(title=title, box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    for header, options in headers:
        table.add_column(header, **options)
    for row in rows:
        table.add_row(*[str(cell) for cell in row])
    console.print(table)
    if footer:
        console.print(footer)
    return True


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def _format_duration(ms: str) -> str:
    """Format milliseconds as human-readable duration."""
    try:
        milliseconds = int(ms)
        seconds = milliseconds // 1000
        minutes = seconds // 60
        hours = minutes // 60

        if hours > 0:
            return f"{hours}h {minutes % 60}m {seconds % 60}s"
        if minutes > 0:
            return f"{minutes}m {seconds % 60}s"
        return f"{seconds}s"
    except (ValueError, TypeError):
        return "Unknown"


def _format_timestamp(timestamp_ms: str) -> str:
    """Format millisecond timestamp as human-readable datetime."""
    try:
        timestamp = int(timestamp_ms) / 1000
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "Unknown"


def format_job_status(job_data: Dict[str, Any]) -> str:
    """Format job status as compact key-value lines.

    Args:
        job_data: Job data from API response

    Returns:
        Formatted string with job status
    """
    status = str(job_data.get("status", "UNKNOWN"))
    lines = ["Job Status"]

    # Core fields
    fields = [
        ("Job ID", job_data.get("job_id", "N/A")),
        ("Name", job_data.get("name", "N/A")),
        ("Status", status),
        ("Running Time", _format_duration(job_data.get("running_time_ms", "0"))),
    ]

    # Optional fields
    if job_data.get("node_count"):
        fields.append(("Nodes", str(job_data["node_count"])))
    if job_data.get("priority"):
        fields.append(("Priority", str(job_data["priority"])))
    if job_data.get("sub_msg"):
        fields.append(("Message", job_data["sub_msg"][:40]))

    # Timeline
    if job_data.get("created_at"):
        fields.append(("Created", _format_timestamp(job_data["created_at"])))
    if job_data.get("finished_at"):
        fields.append(("Finished", _format_timestamp(job_data["finished_at"])))

    for label, value in fields:
        lines.append(f"{label}: {value}")

    return "\n".join(lines)


def format_hpc_status(job_data: Dict[str, Any]) -> str:
    """Format HPC job detail."""
    lines = ["HPC Job"]
    fields = [
        ("Job ID", job_data.get("job_id", "N/A")),
        ("Name", job_data.get("name", "N/A")),
        ("Status", job_data.get("status", "UNKNOWN")),
        ("Compute Group", job_data.get("logic_compute_group_id", "N/A")),
        ("Workspace", job_data.get("workspace_id", "N/A")),
        ("Image", job_data.get("image", "N/A")),
        ("Tasks", job_data.get("number_of_tasks", "N/A")),
        ("CPUs/Task", job_data.get("cpus_per_task", "N/A")),
        ("Mem/CPU", job_data.get("memory_per_cpu", "N/A")),
    ]
    for label, value in fields:
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def format_job_list(jobs: List[Dict[str, Any]]) -> str:
    """Format job list as a table.

    Args:
        jobs: List of job data dictionaries

    Returns:
        Formatted table string
    """
    if not jobs:
        return "No jobs found in local cache."

    # Determine dynamic column widths to avoid truncation while keeping the table aligned.
    job_id_width = max(len("Job ID"), *(len(str(job.get("job_id", "N/A"))) for job in jobs))
    name_width = max(len("Name"), *(len(str(job.get("name", "N/A"))) for job in jobs))
    status_strings = [str(job.get("status", "UNKNOWN")) for job in jobs]
    status_width = (
        max(len("Status"), *(len(s) for s in status_strings)) if status_strings else len("Status")
    )
    created_width = max(len("Created"), *(len(str(job.get("created_at", "N/A"))) for job in jobs))

    header_line = (
        f"{'Job ID':<{job_id_width}} {'Name':<{name_width}} {'Status':<{status_width}} "
        f"{'Created':<{created_width}}"
    )
    separator = "-" * len(header_line)
    lines = ["Jobs", header_line, separator]

    for job, status_str in zip(jobs, status_strings):
        job_id = str(job.get("job_id", "N/A"))
        name = str(job.get("name", "N/A"))
        created = str(job.get("created_at", "N/A"))

        lines.append(
            f"{job_id:<{job_id_width}} {name:<{name_width}} {status_str:<{status_width}} "
            f"{created:<{created_width}}"
        )

    lines.append(separator)
    lines.append(f"Total: {len(jobs)} job(s)")

    return "\n".join(lines)


def print_job_list(jobs: List[Dict[str, Any]]) -> None:
    """Print job list using rich when available."""
    if not jobs:
        print("No jobs found in local cache.")
        return

    rows = [
        [
            str(job.get("job_id", "N/A")),
            str(job.get("name", "N/A")),
            str(job.get("status", "UNKNOWN")),
            str(job.get("created_at", "N/A")),
        ]
        for job in jobs
    ]
    printed = _print_rich_table(
        title="Jobs",
        headers=[
            ("Job ID", {"style": "cyan", "no_wrap": True}),
            ("Name", {"style": "white"}),
            ("Status", {"style": "green"}),
            ("Created", {"style": "magenta"}),
        ],
        rows=rows,
        footer=f"Total: {len(jobs)} job(s)",
    )
    if not printed:
        print(format_job_list(jobs))


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def format_resources(specs: List[Dict[str, Any]], groups: List[Dict[str, Any]]) -> str:
    """Format available resources as a table.

    Args:
        specs: List of resource specifications
        groups: List of compute groups

    Returns:
        Formatted string with resources
    """
    lines = ["Available resources", "GPU configurations:"]

    for spec in specs:
        desc = spec.get("description", f"{spec.get('gpu_count', '?')}x GPU")
        lines.append(f"- {desc}")

    lines.extend(
        [
            "",
            "Compute groups:",
        ]
    )

    for group in groups:
        name = group.get("name", "Unknown")
        location = group.get("location", "")
        lines.append(f"- {name}" + (f" ({location})" if location else ""))

    lines.extend(
        [
            "",
            "Usage:",
            "- --resource 'H200' -> 1x H200 GPU",
            "- --resource '4xH200' -> 4x H200 GPU",
            "- --resource '8 H200' -> 8x H200 GPU",
        ]
    )

    return "\n".join(lines)


def format_nodes(nodes: List[Dict[str, Any]], total: int = 0) -> str:
    """Format cluster nodes as a table.

    Args:
        nodes: List of node data
        total: Total number of nodes (for pagination)

    Returns:
        Formatted table string
    """
    if not nodes:
        return "No nodes found."

    lines = [
        "Cluster nodes",
        f"{'Node ID':<40} {'Pool':<12} {'Status':<12} {'GPUs':<8}",
        "-" * 80,
    ]

    for node in nodes:
        node_id = str(node.get("node_id", "N/A"))[:38]
        pool = node.get("resource_pool", "unknown")
        status = node.get("status", "unknown")
        gpus = str(node.get("gpu_count", "?"))

        lines.append(f"{node_id:<40} {pool:<12} {status:<12} {gpus:<8}")

    lines.append("-" * 80)
    if total:
        lines.append(f"Showing {len(nodes)} of {total} nodes")
    else:
        lines.append(f"Total: {len(nodes)} node(s)")

    return "\n".join(lines)


def print_nodes(nodes: List[Dict[str, Any]], total: int = 0) -> None:
    """Print cluster nodes using rich when available."""
    if not nodes:
        print("No nodes found.")
        return

    rows = [
        [
            str(node.get("node_id", "N/A")),
            str(node.get("resource_pool", "unknown")),
            str(node.get("status", "unknown")),
            str(node.get("gpu_count", "?")),
        ]
        for node in nodes
    ]
    footer = f"Showing {len(nodes)} of {total} nodes" if total else f"Total: {len(nodes)} node(s)"
    printed = _print_rich_table(
        title="Cluster nodes",
        headers=[
            ("Node ID", {"style": "cyan"}),
            ("Pool", {"style": "white"}),
            ("Status", {"style": "green"}),
            ("GPUs", {"justify": "right"}),
        ],
        rows=rows,
        footer=footer,
    )
    if not printed:
        print(format_nodes(nodes, total=total))


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def format_image_list(images: List[Dict[str, Any]]) -> str:
    """Format image list as a table.

    Args:
        images: List of image data dictionaries

    Returns:
        Formatted table string
    """
    if not images:
        return "No images found."

    # Human-readable source labels
    source_labels = {
        "SOURCE_OFFICIAL": "official",
        "SOURCE_PUBLIC": "public",
        "SOURCE_PRIVATE": "private",
    }

    lines = [
        f"{'Name':<30} {'Version':<12} {'Source':<10} {'Status':<10} {'Framework':<14}",
        "-" * 80,
    ]

    for img in images:
        name = str(img.get("name", "N/A"))[:30]
        version = str(img.get("version", ""))[:12]
        raw_source = str(img.get("source", ""))
        source = source_labels.get(raw_source, raw_source)[:10]
        status = str(img.get("status", ""))[:10]
        framework = str(img.get("framework", ""))[:14]

        lines.append(f"{name:<30} {version:<12} {source:<10} {status:<10} {framework:<14}")

    lines.append("-" * 80)
    lines.append(f"Total: {len(images)} image(s)")

    return "\n".join(lines)


def print_image_list(images: List[Dict[str, Any]]) -> None:
    """Print image list using rich when available."""
    if not images:
        print("No images found.")
        return

    source_labels = {
        "SOURCE_OFFICIAL": "official",
        "SOURCE_PUBLIC": "public",
        "SOURCE_PRIVATE": "private",
    }
    rows = [
        [
            str(img.get("name", "N/A")),
            str(img.get("version", "")),
            source_labels.get(str(img.get("source", "")), str(img.get("source", ""))),
            str(img.get("status", "")),
            str(img.get("framework", "")),
        ]
        for img in images
    ]
    printed = _print_rich_table(
        title="Images",
        headers=[
            ("Name", {"style": "cyan"}),
            ("Version", {"style": "white"}),
            ("Source", {"style": "green"}),
            ("Status", {"style": "magenta"}),
            ("Framework", {"style": "yellow"}),
        ],
        rows=rows,
        footer=f"Total: {len(images)} image(s)",
    )
    if not printed:
        print(format_image_list(images))


def format_project_list(projects: List[Dict[str, Any]]) -> str:
    """Format project list as a table.

    Args:
        projects: List of project data dictionaries

    Returns:
        Formatted table string
    """
    if not projects:
        return "No projects found."

    lines = [
        f"{'Name':<24} {'Priority':<10} {'Budget remain':<16}",
        "-" * 52,
    ]

    for proj in projects:
        name = str(proj.get("name", "N/A"))[:24]
        priority = str(proj.get("priority_level", ""))[:10] or "-"
        budget = proj.get("member_remain_budget", 0.0)
        budget_str = f"{budget:,.0f}"

        lines.append(f"{name:<24} {priority:<10} {budget_str:<16}")

    lines.append("-" * 52)
    lines.append(f"Total: {len(projects)} project(s)")

    return "\n".join(lines)


def print_project_list(projects: List[Dict[str, Any]]) -> None:
    """Print project list using rich when available."""
    if not projects:
        print("No projects found.")
        return

    rows = [
        [
            str(proj.get("name", "N/A")),
            str(proj.get("priority_level", "")) or "-",
            f"{proj.get('member_remain_budget', 0.0):,.0f}",
        ]
        for proj in projects
    ]
    printed = _print_rich_table(
        title="Projects",
        headers=[
            ("Name", {"style": "cyan"}),
            ("Priority", {"style": "white"}),
            ("Budget remain", {"justify": "right", "style": "green"}),
        ],
        rows=rows,
        footer=f"Total: {len(projects)} project(s)",
    )
    if not printed:
        print(format_project_list(projects))


def format_image_detail(image_data: Dict[str, Any]) -> str:
    """Format image detail as compact key-value lines.

    Args:
        image_data: Image data dictionary

    Returns:
        Formatted string with image details
    """
    lines = ["Image Detail"]

    # Human-readable source labels
    source_labels = {
        "SOURCE_OFFICIAL": "official",
        "SOURCE_PUBLIC": "public",
        "SOURCE_PRIVATE": "private",
    }

    raw_source = str(image_data.get("source", ""))
    source = source_labels.get(raw_source, raw_source)

    fields = [
        ("Image ID", image_data.get("image_id", "N/A")),
        ("Name", image_data.get("name", "N/A")),
        ("Version", image_data.get("version", "")),
        ("Framework", image_data.get("framework", "")),
        ("Source", source),
        ("Status", image_data.get("status", "")),
        ("URL", image_data.get("url", "")),
        ("Description", image_data.get("description", "")),
        ("Created", image_data.get("created_at", "")),
    ]

    for label, value in fields:
        if value:
            lines.append(f"{label}: {value}")

    return "\n".join(lines)
