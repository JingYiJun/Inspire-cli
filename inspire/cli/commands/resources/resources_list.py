"""Resources list command (availability)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import click

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover - optional dependency fallback
    box = None
    Console = None
    Table = None
    Text = None

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_AUTH_ERROR,
    EXIT_CONFIG_ERROR,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.compute_groups import compute_group_name_map, load_compute_groups_from_config
from inspire.config import Config
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web.resources import (
    KNOWN_COMPUTE_GROUPS,
    clear_availability_cache,
    fetch_resource_availability,
)
from inspire.platform.web.browser_api.core import _browser_api_path, _get_base_url
from inspire.platform.web.session import (
    SessionExpiredError,
    fetch_workspace_availability,
    get_web_session,
    request_json,
)


_ZERO_WORKSPACE_ID = "ws-00000000-0000-0000-0000-000000000000"


@dataclass
class SchedulableSpecSummary:
    workspace_id: str
    workspace_name: str
    group_id: str
    group_name: str
    resource_type: str
    bucket: str
    specs_text: str


def _unique_workspace_ids(values: list[str | None]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        ws_id = str(value or "").strip()
        if not ws_id or ws_id == _ZERO_WORKSPACE_ID or ws_id in seen:
            continue
        seen.add(ws_id)
        unique.append(ws_id)
    return unique


def _workspace_label(config: Optional[Config], session, workspace_id: str) -> str:  # noqa: ANN001
    all_names = getattr(session, "all_workspace_names", None) or {}
    name = str(all_names.get(workspace_id, "")).strip()
    if name:
        return name

    if config is not None:
        if workspace_id == getattr(config, "workspace_cpu_id", None):
            return "cpu"
        if workspace_id == getattr(config, "workspace_gpu_id", None):
            return "gpu"
        if workspace_id == getattr(config, "workspace_internet_id", None):
            return "internet"
        if workspace_id == getattr(config, "job_workspace_id", None):
            return "default"

        for alias, value in (getattr(config, "workspaces", None) or {}).items():
            if value == workspace_id:
                return str(alias)

    return workspace_id


def _load_optional_config() -> Optional[Config]:
    try:
        config, _ = Config.from_files_and_env(require_credentials=False)
        return config
    except Exception:
        return None


def _resolve_resource_workspace_ids(
    config: Optional[Config],
    session,  # noqa: ANN001
    *,
    all_workspaces: bool,
) -> list[str]:
    candidates: list[str | None] = []

    if config is not None:
        candidates.extend(
            [
                getattr(config, "workspace_cpu_id", None),
                getattr(config, "workspace_gpu_id", None),
                getattr(config, "workspace_internet_id", None),
                getattr(config, "job_workspace_id", None),
            ]
        )
        config_workspaces = getattr(config, "workspaces", None)
        if isinstance(config_workspaces, dict):
            candidates.extend(config_workspaces.values())

    candidates.append(getattr(session, "workspace_id", None))

    if all_workspaces:
        candidates.extend(getattr(session, "all_workspace_ids", None) or [])

    return _unique_workspace_ids(candidates)


def _annotate_workspace(entry, *, workspace_id: str, workspace_name: str) -> None:  # noqa: ANN001
    entry.workspace_id = workspace_id
    entry.workspace_name = workspace_name


def _collect_accurate_availability(
    *,
    session,  # noqa: ANN001
    config: Optional[Config],
    workspace_ids: list[str],
) -> tuple[list, list[tuple[str, str]]]:
    if not workspace_ids:
        return browser_api_module.get_accurate_gpu_availability(session=session), []

    availability: list = []
    errors: list[tuple[str, str]] = []

    for ws_id in workspace_ids:
        ws_label = _workspace_label(config, session, ws_id)
        try:
            entries = browser_api_module.get_accurate_gpu_availability(
                workspace_id=ws_id,
                session=session,
            )
        except Exception as exc:
            errors.append((ws_label, str(exc)))
            continue

        for entry in entries:
            _annotate_workspace(entry, workspace_id=ws_id, workspace_name=ws_label)
        availability.extend(entries)

    return availability, errors


def _collect_workspace_availability(
    *,
    config: Optional[Config],
    session,  # noqa: ANN001
    workspace_ids: list[str],
    show_all: bool,
    no_cache: bool,
) -> tuple[list, list[tuple[str, str]]]:
    if no_cache:
        clear_availability_cache()

    if not workspace_ids:
        return (
            fetch_resource_availability(
                config=config,
                known_only=not show_all,
            ),
            [],
        )

    availability: list = []
    errors: list[tuple[str, str]] = []

    for ws_id in workspace_ids:
        ws_label = _workspace_label(config, session, ws_id)
        try:
            entries = fetch_resource_availability(
                config=config,
                known_only=not show_all,
                workspace_id=ws_id,
            )
        except Exception as exc:
            errors.append((ws_label, str(exc)))
            continue

        for entry in entries:
            _annotate_workspace(entry, workspace_id=ws_id, workspace_name=ws_label)
        availability.extend(entries)

    return availability, errors


def _emit_workspace_warnings(ctx: Context, errors: list[tuple[str, str]]) -> None:
    if not errors or ctx.json_output:
        return
    for workspace_label, message in errors:
        click.echo(f"Warning: workspace {workspace_label} failed: {message}", err=True)


def _workspace_column_enabled(availability: list) -> bool:
    workspace_ids = {
        str(getattr(item, "workspace_id", "")).strip()
        for item in availability
        if str(getattr(item, "workspace_id", "")).strip()
    }
    return len(workspace_ids) > 1


def _status_text(free_gpus: int) -> Text:
    if Text is None:
        if free_gpus >= 100:
            return "充足"  # type: ignore[return-value]
        if free_gpus >= 32:
            return "较多"  # type: ignore[return-value]
        if free_gpus >= 8:
            return "一般"  # type: ignore[return-value]
        if free_gpus > 0:
            return "紧张"  # type: ignore[return-value]
        return "无空闲"  # type: ignore[return-value]
    if free_gpus >= 100:
        return Text("充足", style="green")
    if free_gpus >= 32:
        return Text("较多", style="bright_green")
    if free_gpus >= 8:
        return Text("一般", style="yellow")
    if free_gpus > 0:
        return Text("紧张", style="yellow")
    return Text("无空闲", style="red")


def _resource_note_lines(availability: list) -> list[str]:
    negative_items = [item for item in availability if getattr(item, "available_gpus", 0) < 0]
    if not negative_items:
        return []

    return [
        "[yellow]说明：部分资源组出现 `used > total`，这是平台接口返回的统计异常或短暂超卖。[/yellow]",
        "[yellow]CLI 的 JSON 输出保留原始值；人类表格中已按 0 展示 Available，避免误读。[/yellow]",
    ]


def _make_console() -> Console:
    terminal_width = shutil.get_terminal_size((120, 24)).columns
    return Console(width=max(120, terminal_width))


def _workspace_alias_by_id(config: Optional[Config]) -> dict[str, str]:
    if config is None:
        return {}

    alias_map: dict[str, str] = {}
    for alias, workspace_id in (getattr(config, "workspaces", None) or {}).items():
        ws_id = str(workspace_id or "").strip()
        if ws_id:
            alias_map[ws_id] = str(alias)

    explicit_aliases = {
        "cpu": getattr(config, "workspace_cpu_id", None),
        "gpu": getattr(config, "workspace_gpu_id", None),
        "internet": getattr(config, "workspace_internet_id", None),
    }
    for alias, workspace_id in explicit_aliases.items():
        ws_id = str(workspace_id or "").strip()
        if ws_id:
            alias_map.setdefault(ws_id, alias)

    return alias_map


def _resource_bucket(item, config: Optional[Config]) -> str:  # noqa: ANN001
    alias = _workspace_alias_by_id(config).get(str(getattr(item, "workspace_id", "")).strip(), "")
    resource_type = str(getattr(item, "gpu_type", "") or "").upper()

    if alias in {"ascend"} or "ASCEND" in resource_type:
        return "ascend"
    if alias in {"cpu", "hpc"}:
        return "cpu"
    if alias in {"gpu", "internet"}:
        return "gpu"
    if resource_type == "CPU":
        return "cpu"
    return "gpu"


def _display_available(item) -> int:  # noqa: ANN001
    return max(int(getattr(item, "available_gpus", 0)), 0)


def _resource_type_sort_key(item) -> tuple[int, str]:  # noqa: ANN001
    resource_type = str(getattr(item, "gpu_type", "") or "").strip()
    normalized = resource_type.upper()
    if not resource_type or resource_type == "-" or normalized in {"UNKNOWN", "UNKNOWN GPU"}:
        return (1, "")
    return (0, normalized)


def _sorted_accurate_availability(availability: list) -> list:
    return sorted(
        availability,
        key=lambda item: (
            str(getattr(item, "workspace_name", "") or ""),
            _resource_type_sort_key(item),
            -_display_available(item),
            str(getattr(item, "group_name", "") or ""),
        ),
    )


def _collect_cpu_workspace_summaries(
    *,
    config: Optional[Config],
    session,  # noqa: ANN001
) -> list:
    if config is None:
        return []

    known_groups = {}
    if getattr(config, "compute_groups", None):
        groups_tuple = load_compute_groups_from_config(config.compute_groups)
        known_groups = compute_group_name_map(groups_tuple)
    alias_by_id = _workspace_alias_by_id(config)
    results: list = []

    for workspace_id, alias in alias_by_id.items():
        if alias not in {"cpu", "hpc"}:
            continue

        workspace_name = _workspace_label(config, session, workspace_id)
        try:
            nodes = fetch_workspace_availability(
                session,
                base_url=getattr(config, "base_url", None) or "",
                workspace_id=workspace_id,
            )
        except Exception:
            continue

        groups: dict[str, dict] = {}
        for node in nodes:
            group_id = str(node.get("logic_compute_group_id", "") or "").strip()
            if not group_id:
                continue

            cpu_count = int(node.get("cpu_count") or 0)
            if cpu_count <= 0:
                continue

            group_name = str(node.get("logic_compute_group_name", "") or "").strip()
            if not group_name:
                group_name = known_groups.get(group_id, group_id)

            group = groups.setdefault(
                group_id,
                {
                    "group_id": group_id,
                    "group_name": group_name,
                    "total_cpus": 0,
                    "free_cpus": 0,
                },
            )
            group["total_cpus"] += cpu_count

            resource_pool = str(node.get("resource_pool", "unknown")).lower()
            is_free = (
                len(node.get("task_list") or []) == 0
                and not str(node.get("cordon_type", "")).strip()
                and not node.get("is_maint", False)
                and resource_pool != "fault"
            )
            if is_free:
                group["free_cpus"] += cpu_count

        for item in groups.values():
            total_cpus = int(item["total_cpus"])
            free_cpus = int(item["free_cpus"])
            entry = browser_api_module.GPUAvailability(
                group_id=item["group_id"],
                group_name=item["group_name"],
                gpu_type="CPU",
                total_gpus=total_cpus,
                used_gpus=max(total_cpus - free_cpus, 0),
                available_gpus=free_cpus,
                low_priority_gpus=0,
            )
            _annotate_workspace(entry, workspace_id=workspace_id, workspace_name=workspace_name)
            results.append(entry)

    return results


def _collect_cpu_resources(
    *,
    config: Optional[Config],
    session,  # noqa: ANN001
) -> list:
    """Backward-compatible wrapper for CPU workspace summaries."""
    return _collect_cpu_workspace_summaries(config=config, session=session)


def _parse_schedule_quotas(schedule: dict | None) -> list[dict]:
    quota_list = (schedule or {}).get("quota", [])
    if isinstance(quota_list, str):
        try:
            quota_list = json.loads(quota_list) if quota_list else []
        except Exception:
            quota_list = []
    if not isinstance(quota_list, list):
        return []
    return [item for item in quota_list if isinstance(item, dict)]


def _extract_spec_sizes_from_prices(prices: list[dict], bucket: str) -> list[int]:
    sizes: set[int] = set()
    for price in prices:
        gpu_count = int(price.get("gpu_count") or 0)
        cpu_count = int(price.get("cpu_count") or 0)
        if bucket == "cpu":
            if gpu_count == 0 and cpu_count > 0:
                sizes.add(cpu_count)
            continue
        if gpu_count > 0:
            sizes.add(gpu_count)
    return sorted(sizes, reverse=True)


def _extract_spec_sizes_from_schedule(schedule: dict | None, bucket: str) -> list[int]:
    sizes: set[int] = set()
    for quota in _parse_schedule_quotas(schedule):
        gpu_count = int(quota.get("gpu_count") or 0)
        cpu_count = int(quota.get("cpu_count") or 0)
        if bucket == "cpu":
            if gpu_count == 0 and cpu_count > 0:
                sizes.add(cpu_count)
            continue
        if gpu_count > 0:
            sizes.add(gpu_count)
    return sorted(sizes, reverse=True)


def _fallback_spec_sizes_from_nodes(nodes: list[dict], bucket: str) -> list[int]:
    if bucket == "cpu":
        totals = {
            max(
                int(((node.get("cpu") or {}).get("total")) or 0),
                int(node.get("cpu_count") or 0),
            )
            for node in nodes
        }
        return sorted({value for value in totals if value > 0}, reverse=True)

    max_total = max(
        (
            max(
                int(((node.get("gpu") or {}).get("total")) or 0),
                int(node.get("gpu_count") or 0),
            )
            for node in nodes
        ),
        default=0,
    )
    sizes: list[int] = []
    current = max_total
    while current > 0:
        sizes.append(current)
        current //= 2
    if 1 not in sizes and max_total > 0:
        sizes.append(1)
    return sorted(set(size for size in sizes if size > 0), reverse=True)


def _schedulable_node(node: dict) -> bool:
    status = str(node.get("status", "") or "").upper()
    cordon_type = str(node.get("cordon_type", "") or "").strip()
    resource_pool = str(node.get("resource_pool", "") or "").lower()
    return status == "READY" and not cordon_type and resource_pool != "fault"


def _node_available_units(node: dict, bucket: str) -> int:
    if bucket == "cpu":
        cpu = node.get("cpu") or {}
        return max(int(cpu.get("available") or 0), 0)
    gpu = node.get("gpu") or {}
    return max(int(gpu.get("available") or 0), 0)


def _bucket_available_units(available_units: int, spec_sizes: list[int]) -> int | None:
    if available_units <= 0:
        return None
    for size in spec_sizes:
        if size <= available_units:
            return size
    return None


def _format_spec_label(size: int, bucket: str) -> str:
    suffix = "核" if bucket == "cpu" else "卡"
    return f"{size}{suffix}"


def _format_spec_counts(spec_sizes: list[int], bucket_counts: dict[int, int], bucket: str) -> str:
    if not spec_sizes:
        return "-"
    visible_parts = [
        f"{_format_spec_label(size, bucket)}×{bucket_counts.get(size, 0)}"
        for size in spec_sizes
        if bucket_counts.get(size, 0) > 0
    ]
    return " · ".join(visible_parts) if visible_parts else "-"


def _fetch_group_node_dimensions(
    *,
    session,  # noqa: ANN001
    workspace_id: str,
    group_id: str,
    base_url: str,
) -> list[dict]:
    node_dimensions: list[dict] = []
    page_num = 1
    page_size = 500
    url = f"{base_url}{_browser_api_path('/cluster_metric/list_node_dimension')}"
    headers = {"Referer": f"{base_url}/jobs/spacesOverview?spaceId={workspace_id}"}

    while True:
        payload = request_json(
            session,
            "POST",
            url,
            headers=headers,
            body={
                "page_num": page_num,
                "page_size": page_size,
                "filter": {
                    "workspace_id": workspace_id,
                    "logic_compute_group_id": group_id,
                },
            },
            timeout=30,
        )
        batch = payload.get("data", {}).get("node_dimensions", [])
        if not isinstance(batch, list) or not batch:
            break
        node_dimensions.extend(batch)
        if len(batch) < page_size:
            break
        page_num += 1

    return node_dimensions


def _collect_schedulable_spec_summaries(
    availability: list,
    *,
    config: Optional[Config],
    session,  # noqa: ANN001
) -> list[SchedulableSpecSummary]:
    if not availability:
        return []

    base_url = (_get_base_url() or "").rstrip("/")
    price_cache: dict[tuple[str, str], list[dict]] = {}
    schedule_cache: dict[str, dict] = {}
    node_cache: dict[tuple[str, str], list[dict]] = {}
    summaries: list[SchedulableSpecSummary] = []

    for item in _sorted_accurate_availability(availability):
        workspace_id = str(getattr(item, "workspace_id", "") or "").strip()
        group_id = str(getattr(item, "group_id", "") or "").strip()
        if not workspace_id or not group_id:
            continue

        bucket = _resource_bucket(item, config)
        cache_key = (workspace_id, group_id)
        nodes = node_cache.get(cache_key)
        if nodes is None:
            try:
                nodes = _fetch_group_node_dimensions(
                    session=session,
                    workspace_id=workspace_id,
                    group_id=group_id,
                    base_url=base_url,
                )
            except Exception:
                nodes = []
            node_cache[cache_key] = nodes

        if not nodes:
            specs_text = "-"
        else:
            prices = price_cache.get(cache_key)
            if prices is None:
                try:
                    prices = browser_api_module.get_resource_prices(
                        workspace_id=workspace_id,
                        logic_compute_group_id=group_id,
                        session=session,
                    )
                except Exception:
                    prices = []
                price_cache[cache_key] = prices

            spec_sizes = _extract_spec_sizes_from_prices(prices, bucket)
            if not spec_sizes:
                if workspace_id not in schedule_cache:
                    try:
                        schedule_cache[workspace_id] = browser_api_module.get_notebook_schedule(
                            workspace_id=workspace_id,
                            session=session,
                        )
                    except Exception:
                        schedule_cache[workspace_id] = {}
                spec_sizes = _extract_spec_sizes_from_schedule(schedule_cache[workspace_id], bucket)
            if not spec_sizes:
                spec_sizes = _fallback_spec_sizes_from_nodes(nodes, bucket)

            bucket_counts = {size: 0 for size in spec_sizes}
            for node in nodes:
                if not _schedulable_node(node):
                    continue
                available_units = _node_available_units(node, bucket)
                size = _bucket_available_units(available_units, spec_sizes)
                if size is None:
                    continue
                bucket_counts[size] = bucket_counts.get(size, 0) + 1

            specs_text = _format_spec_counts(spec_sizes, bucket_counts, bucket)

        summaries.append(
            SchedulableSpecSummary(
                workspace_id=workspace_id,
                workspace_name=str(getattr(item, "workspace_name", "") or "-"),
                group_id=group_id,
                group_name=str(getattr(item, "group_name", "") or "-"),
                resource_type=str(getattr(item, "gpu_type", "") or "-"),
                bucket=bucket,
                specs_text=specs_text,
            )
        )

    return summaries


def _render_accurate_fallback(
    availability: list,
    config: Optional[Config],
    *,
    specs_by_key: dict[tuple[str, str], str] | None = None,
) -> None:
    bucket_titles = {
        "gpu": "GPU Resources",
        "cpu": "CPU Resources",
        "ascend": "ASCEND Resources",
    }
    bucketed = {key: [] for key in bucket_titles}
    for item in _sorted_accurate_availability(availability):
        bucketed[_resource_bucket(item, config)].append(item)
    specs_by_key = specs_by_key or {}

    for bucket in ("gpu", "cpu", "ascend"):
        items = bucketed[bucket]
        if not items:
            continue
        click.echo(f"\n{bucket_titles[bucket]}")
        for item in items:
            workspace_prefix = ""
            if _workspace_column_enabled(availability):
                workspace_prefix = f"[{getattr(item, 'workspace_name', '-')}] "
            specs_text = specs_by_key.get(
                (
                    str(getattr(item, "workspace_id", "") or ""),
                    str(getattr(item, "group_id", "") or ""),
                ),
                "-",
            )
            click.echo(
                f"{workspace_prefix}{item.group_name}: {item.gpu_type or '-'} | "
                f"available={max(item.available_gpus, 0)} used={item.used_gpus} "
                f"low_pri={item.low_priority_gpus} total={item.total_gpus} specs={specs_text}"
            )


def _render_accurate_rich_table(
    console: Console,
    items: list,
    *,
    title: str,
    show_workspace: bool,
    specs_by_key: dict[tuple[str, str], str] | None = None,
) -> None:
    table = Table(
        title=title,
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
    )
    if show_workspace:
        table.add_column("Workspace", style="cyan", no_wrap=True)
    table.add_column("Resource Type", style="green")
    table.add_column("Compute Group", style="white")
    table.add_column("Available", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Low Pri", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Schedulable Specs", style="yellow")
    table.add_column("Status", justify="center", no_wrap=True)

    total_available = 0
    total_used = 0
    total_low_pri = 0
    total_gpus = 0

    for item in items:
        actual_available = int(getattr(item, "available_gpus", 0))
        display_available = max(actual_available, 0)
        row = []
        if show_workspace:
            row.append(str(getattr(item, "workspace_name", "") or "-"))
        specs_text = "-"
        if specs_by_key is not None:
            specs_text = specs_by_key.get(
                (
                    str(getattr(item, "workspace_id", "") or ""),
                    str(getattr(item, "group_id", "") or ""),
                ),
                "-",
            )
        row.extend(
            [
                str(item.gpu_type or "-"),
                str(item.group_name or "-"),
                str(display_available),
                str(item.used_gpus),
                str(item.low_priority_gpus),
                str(item.total_gpus),
                specs_text,
                _status_text(display_available),
            ]
        )
        table.add_row(*row)

        total_available += display_available
        total_used += item.used_gpus
        total_low_pri += item.low_priority_gpus
        total_gpus += item.total_gpus

    table.add_section()
    if show_workspace:
        table.add_row(
            "TOTAL",
            "",
            "",
            str(max(total_available, 0)),
            str(total_used),
            str(total_low_pri),
            str(total_gpus),
            "",
            "",
        )
    else:
        table.add_row(
            "TOTAL",
            "",
            str(max(total_available, 0)),
            str(total_used),
            str(total_low_pri),
            str(total_gpus),
            "",
            "",
        )

    console.print(table)


def _known_compute_groups_from_config(*, show_all: bool) -> dict[str, str]:
    known_groups = KNOWN_COMPUTE_GROUPS
    if show_all:
        return known_groups

    try:
        config, _ = Config.from_files_and_env(require_credentials=False)
        if config.compute_groups:
            groups_tuple = load_compute_groups_from_config(config.compute_groups)
            return compute_group_name_map(groups_tuple)
    except Exception:
        return known_groups
    return known_groups


def _format_availability_table(
    availability,
    workspace_mode: bool = False,
    config: Optional[Config] = None,
) -> None:
    if Console is None or Table is None or box is None:
        title = "GPU Availability · 节点视角" if workspace_mode else "GPU Availability"
        click.echo(f"\n{title}")
        for item in availability:
            workspace_prefix = ""
            if _workspace_column_enabled(availability):
                workspace_prefix = f"[{getattr(item, 'workspace_name', '-')}] "
            click.echo(
                f"{workspace_prefix}{item.group_name}: {item.gpu_type or '-'} | "
                f"ready={item.ready_nodes} free_nodes={item.free_nodes} free_gpus={max(item.free_gpus, 0)}"
            )
        for note in _resource_note_lines(availability):
            click.echo(note.replace("[yellow]", "").replace("[/yellow]", ""))
        click.echo("")
        return

    console = _make_console()
    title = "GPU Availability · 节点视角" if workspace_mode else "GPU Availability"
    show_workspace = _workspace_column_enabled(availability)

    console.print()
    console.print(f"[bold cyan]{title}[/bold cyan]")
    if workspace_mode:
        subtitle = "范围：已配置 workspace 聚合" if show_workspace else "范围：当前 workspace"
        console.print(f"[dim]{subtitle}[/dim]")

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold magenta")
    if show_workspace:
        table.add_column("Workspace", style="cyan", no_wrap=True)
    table.add_column("Resource Type", style="green")
    table.add_column("Compute Group", style="white")
    table.add_column("Ready", justify="right")
    table.add_column("Free Nodes", justify="right")
    table.add_column("Free GPUs", justify="right")
    table.add_column("Status", justify="center", no_wrap=True)

    for item in availability:
        row = []
        if show_workspace:
            row.append(str(getattr(item, "workspace_name", "") or "-"))
        free_gpus = max(int(getattr(item, "free_gpus", 0)), 0)
        row.extend(
            [
                str(item.gpu_type or "-"),
                str(item.group_name or "-"),
                str(item.ready_nodes),
                str(item.free_nodes),
                str(free_gpus),
                _status_text(free_gpus),
            ]
        )
        table.add_row(*row)

    console.print(table)
    console.print("[bold]用法[/bold]")
    console.print('  • `inspire run "python train.py"` 自动选择资源')
    console.print('  • `inspire run "python train.py" --type H100` 优先 H100')
    console.print('  • `inspire run "python train.py" --gpus 4` 申请 4 卡')
    for note in _resource_note_lines(availability):
        console.print(note)
    console.print()


def _format_accurate_availability_table(
    availability,
    *,
    config: Optional[Config] = None,
    session=None,  # noqa: ANN001
) -> None:
    bucket_titles = {
        "gpu": "GPU Resources",
        "cpu": "CPU Resources",
        "ascend": "ASCEND Resources",
    }
    bucketed = {key: [] for key in bucket_titles}
    for item in _sorted_accurate_availability(availability):
        bucketed[_resource_bucket(item, config)].append(item)
    specs_by_key: dict[tuple[str, str], str] = {}
    if session is not None:
        specs_by_key = {
            (summary.workspace_id, summary.group_id): summary.specs_text
            for summary in _collect_schedulable_spec_summaries(
                availability,
                config=config,
                session=session,
            )
        }

    if Console is None or Table is None or box is None:
        _render_accurate_fallback(availability, config, specs_by_key=specs_by_key)
        for note in _resource_note_lines(availability):
            click.echo(note.replace("[yellow]", "").replace("[/yellow]", ""))
        click.echo("")
        return

    console = _make_console()
    show_workspace = _workspace_column_enabled(availability)

    for bucket in ("gpu", "cpu", "ascend"):
        items = bucketed[bucket]
        if not items:
            continue
        _render_accurate_rich_table(
            console,
            items,
            title=bucket_titles[bucket],
            show_workspace=show_workspace,
            specs_by_key=specs_by_key,
        )

    console.print("[bold]说明[/bold]")
    console.print("  • Available：当前空闲可用 GPU")
    console.print("  • Used：正在被任务占用的 GPU")
    console.print("  • Low Pri：低优先级占用，可被抢占")
    console.print("  • Schedulable Specs：按单机剩余资源向下归到最大合法 spec；单机只计一次。")
    console.print("[bold]用法[/bold]")
    console.print('  • `inspire run "python train.py"` 自动选择资源')
    console.print('  • `inspire run "python train.py" --type H100` 优先 H100')
    console.print('  • `inspire run "python train.py" --gpus 4` 申请 4 卡')
    for note in _resource_note_lines(availability):
        console.print(note)
    console.print()


def _list_accurate_resources(ctx: Context, show_all: bool, all_workspaces: bool) -> None:
    """List accurate GPU availability using browser API."""
    try:
        known_groups = _known_compute_groups_from_config(show_all=show_all)
        config = _load_optional_config()
        session = get_web_session()
        workspace_ids = _resolve_resource_workspace_ids(
            config,
            session,
            all_workspaces=all_workspaces,
        )
        availability, workspace_errors = _collect_accurate_availability(
            session=session,
            config=config,
            workspace_ids=workspace_ids,
        )

        if not show_all:
            availability = [a for a in availability if a.group_id in known_groups]
            for entry in availability:
                if not entry.group_name:
                    entry.group_name = known_groups.get(entry.group_id, entry.group_name)

        cpu_summaries = _collect_cpu_resources(config=config, session=session)
        if cpu_summaries:
            availability = [
                item for item in availability if _resource_bucket(item, config) != "cpu"
            ]
            availability.extend(cpu_summaries)

        _emit_workspace_warnings(ctx, workspace_errors)

        if not availability:
            if ctx.json_output:
                click.echo(json_formatter.format_json({"availability": []}))
            else:
                click.echo(human_formatter.format_error("No GPU resources found"))
            return

        if ctx.json_output:
            output = [
                {
                    "group_id": a.group_id,
                    "group_name": a.group_name,
                    "gpu_type": a.gpu_type,
                    "total_gpus": a.total_gpus,
                    "used_gpus": a.used_gpus,
                    "available_gpus": a.available_gpus,
                    "low_priority_gpus": a.low_priority_gpus,
                    "workspace_id": getattr(a, "workspace_id", ""),
                    "workspace_name": getattr(a, "workspace_name", ""),
                }
                for a in availability
            ]
            click.echo(json_formatter.format_json({"availability": output}))
        else:
            _format_accurate_availability_table(
                availability,
                config=config,
                session=session,
            )

    except (SessionExpiredError, ValueError) as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


def _list_workspace_resources(
    ctx: Context,
    show_all: bool,
    no_cache: bool,
    all_workspaces: bool,
) -> None:
    """List workspace-specific GPU availability using browser API."""
    try:
        config = _load_optional_config()
        session = get_web_session(require_workspace=True)
        workspace_ids = _resolve_resource_workspace_ids(
            config,
            session,
            all_workspaces=all_workspaces,
        )
        availability, workspace_errors = _collect_workspace_availability(
            config=config,
            session=session,
            workspace_ids=workspace_ids,
            show_all=show_all,
            no_cache=no_cache,
        )
        _emit_workspace_warnings(ctx, workspace_errors)

        if not availability:
            click.echo(
                human_formatter.format_error("No GPU resources found in configured workspaces")
            )
            return

        if ctx.json_output:
            output = [
                {
                    "group_id": a.group_id,
                    "group_name": a.group_name,
                    "gpu_type": a.gpu_type,
                    "gpus_per_node": a.gpu_per_node,
                    "total_nodes": a.total_nodes,
                    "ready_nodes": a.ready_nodes,
                    "free_nodes": a.free_nodes,
                    "free_gpus": a.free_gpus,
                    "workspace_id": getattr(a, "workspace_id", ""),
                    "workspace_name": getattr(a, "workspace_name", ""),
                }
                for a in availability
            ]
            click.echo(json_formatter.format_json({"availability": output}))
            return

        _format_availability_table(availability, workspace_mode=True, config=config)

    except (SessionExpiredError, ValueError) as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


def _progress_bar(current: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "░" * width
    filled = int(width * current / total)
    return "█" * filled + "░" * (width - filled)


def _render_nodes_display(
    availability: list,
    *,
    phase: str,
    timestamp: str,
    interval: int,
    progress_state: dict[str, int],
) -> None:
    os.system("clear")

    if phase == "fetching":
        fetched = progress_state["fetched"]
        total = progress_state["total"] or 1
        bar = _progress_bar(fetched, total)
        if total > 1:
            click.echo(f"🔄 [{bar}] Fetching {fetched}/{total} nodes...\n")
        else:
            click.echo(f"🔄 [{bar}] Fetching availability...\n")
    else:
        bar = _progress_bar(1, 1)
        click.echo(f"✅ [{bar}] Updated at {timestamp} (Workspace) (interval: {interval}s)\n")

    if not availability:
        if phase != "fetching":
            click.echo("No GPU resources found")
        return

    show_workspace = _workspace_column_enabled(availability)
    separator_width = 79 if show_workspace else 60
    click.echo("─" * separator_width)
    if show_workspace:
        click.echo(
            f"{'Workspace':<18} {'GPU':<6} {'Location':<24} {'Ready':>8} {'Free':>8} {'GPUs':>8}"
        )
    else:
        click.echo(f"{'GPU':<6} {'Location':<24} {'Ready':>8} {'Free':>8} {'GPUs':>8}")
    click.echo("─" * separator_width)

    total_free = 0
    for a in availability:
        workspace_name = str(getattr(a, "workspace_name", "") or "")[:17]
        location = a.group_name[:23]
        gpu = a.gpu_type[:5]
        free_gpus = a.free_gpus
        total_free += free_gpus

        if free_gpus >= 64:
            indicator = "🟢"
        elif free_gpus >= 16:
            indicator = "🟡"
        elif free_gpus > 0:
            indicator = "🟠"
        else:
            indicator = "🔴"

        if show_workspace:
            click.echo(
                f"{workspace_name:<18} {gpu:<6} {location:<24} {a.ready_nodes:>8} "
                f"{a.free_nodes:>8} {free_gpus:>8} {indicator}"
            )
        else:
            click.echo(
                f"{gpu:<6} {location:<24} {a.ready_nodes:>8} {a.free_nodes:>8} "
                f"{free_gpus:>8} {indicator}"
            )

    click.echo("─" * separator_width)
    if show_workspace:
        click.echo(f"{'Total':<18} {'':<6} {'':<24} {'':>8} {'':>8} {total_free:>8}")
    else:
        click.echo(f"{'Total':<6} {'':<24} {'':>8} {'':>8} {total_free:>8}")
    click.echo("")
    click.echo("Ctrl+C to stop")


def _render_accurate_display(
    availability: list,
    *,
    phase: str,
    timestamp: str,
    interval: int,
) -> None:
    os.system("clear")

    if phase == "fetching":
        click.echo("🔄 Fetching accurate availability...\n")
    else:
        click.echo(f"✅ Updated at {timestamp} (Accurate) (interval: {interval}s)\n")

    if not availability:
        if phase != "fetching":
            click.echo("No GPU resources found")
        return

    show_workspace = _workspace_column_enabled(availability)
    separator_width = 114 if show_workspace else 95
    if show_workspace:
        lines = [
            "─" * separator_width,
            (
                f"{'Workspace':<18} {'GPU Type':<22} {'Compute Group':<25} {'Available':>10} "
                f"{'Used':>8} {'Low Pri':>8} {'Total':>8}"
            ),
            "─" * separator_width,
        ]
    else:
        lines = [
            "─" * separator_width,
            (
                f"{'GPU Type':<22} {'Compute Group':<25} {'Available':>10} "
                f"{'Used':>8} {'Low Pri':>8} {'Total':>8}"
            ),
            "─" * separator_width,
        ]

    sorted_avail = sorted(availability, key=lambda x: x.available_gpus, reverse=True)

    total_available = 0
    total_used = 0
    total_low_pri = 0
    total_gpus = 0

    for a in sorted_avail:
        workspace_name = str(getattr(a, "workspace_name", "") or "")[:17]
        gpu_type = a.gpu_type[:21]
        location = a.group_name[:24]
        free_gpus = a.available_gpus

        if free_gpus >= 100:
            status = "✓"
        elif free_gpus >= 32:
            status = "○"
        elif free_gpus >= 8:
            status = "◐"
        elif free_gpus > 0:
            status = "⚠"
        else:
            status = "✗"

        if show_workspace:
            lines.append(
                f"{workspace_name:<18} {gpu_type:<22} {location:<25} {a.available_gpus:>10} "
                f"{a.used_gpus:>8} {a.low_priority_gpus:>8} {a.total_gpus:>8} {status}"
            )
        else:
            lines.append(
                f"{gpu_type:<22} {location:<25} {a.available_gpus:>10} {a.used_gpus:>8} "
                f"{a.low_priority_gpus:>8} {a.total_gpus:>8} {status}"
            )

        total_available += a.available_gpus
        total_used += a.used_gpus
        total_low_pri += a.low_priority_gpus
        total_gpus += a.total_gpus

    lines.append("─" * separator_width)
    if show_workspace:
        lines.append(
            f"{'TOTAL':<18} {'':<22} {'':<25} {total_available:>10} {total_used:>8} "
            f"{total_low_pri:>8} {total_gpus:>8}"
        )
    else:
        lines.append(
            f"{'TOTAL':<22} {'':<25} {total_available:>10} {total_used:>8} "
            f"{total_low_pri:>8} {total_gpus:>8}"
        )
    lines.append("")
    lines.append("Ctrl+C to stop")

    click.echo("\n".join(lines))


def _render_display(
    *,
    mode: str,
    availability: list,
    phase: str,
    timestamp: str,
    interval: int,
    progress_state: dict[str, int],
) -> None:
    if mode == "nodes":
        _render_nodes_display(
            availability,
            phase=phase,
            timestamp=timestamp,
            interval=interval,
            progress_state=progress_state,
        )
    else:
        _render_accurate_display(availability, phase=phase, timestamp=timestamp, interval=interval)


def _watch_resources(
    ctx: Context,
    show_all: bool,
    all_workspaces: bool,
    interval: int,
    workspace: bool,
    use_global: bool,
) -> None:
    api_logger = logging.getLogger("inspire.inspire_api_control")
    original_level = api_logger.level
    api_logger.setLevel(logging.CRITICAL)

    mode = "nodes" if workspace or use_global else "accurate"

    try:
        session = get_web_session(require_workspace=(mode == "nodes"))
    except Exception as e:
        click.echo(human_formatter.format_error(f"Failed to get web session: {e}"), err=True)
        sys.exit(EXIT_AUTH_ERROR)

    progress_state = {"fetched": 0, "total": 0}
    config = _load_optional_config()
    workspace_ids = _resolve_resource_workspace_ids(
        config,
        session,
        all_workspaces=all_workspaces,
    )

    def on_progress(fetched: int, total: int) -> None:
        if mode != "nodes":
            return
        progress_state["fetched"] = fetched
        progress_state["total"] = total
        now = datetime.now().strftime("%H:%M:%S")
        _render_display(
            mode=mode,
            availability=availability,
            phase="fetching",
            timestamp=now,
            interval=interval,
            progress_state=progress_state,
        )

    try:
        availability: list = []
        while True:
            progress_state["fetched"] = 0
            progress_state["total"] = 0

            now = datetime.now().strftime("%H:%M:%S")
            _render_display(
                mode=mode,
                availability=availability,
                phase="fetching",
                timestamp=now,
                interval=interval,
                progress_state=progress_state,
            )

            try:
                if mode == "nodes":
                    availability, workspace_errors = _collect_workspace_availability(
                        config=config,
                        session=session,
                        workspace_ids=workspace_ids,
                        show_all=show_all,
                        no_cache=True,
                    )
                else:
                    availability, workspace_errors = _collect_accurate_availability(
                        session=session,
                        config=config,
                        workspace_ids=workspace_ids,
                    )
                    known_groups = _known_compute_groups_from_config(show_all=show_all)
                    if not show_all:
                        availability = [a for a in availability if a.group_id in known_groups]
                        for entry in availability:
                            if not entry.group_name:
                                entry.group_name = known_groups.get(
                                    entry.group_id, entry.group_name
                                )
                if workspace_errors:
                    for workspace_label, message in workspace_errors:
                        click.echo(
                            f"Warning: workspace {workspace_label} failed: {message}",
                            err=True,
                        )
            except (SessionExpiredError, ValueError) as e:
                api_logger.setLevel(original_level)
                click.echo(human_formatter.format_error(str(e)), err=True)
                sys.exit(EXIT_AUTH_ERROR)
            except Exception as e:
                os.system("clear")
                click.echo(f"⚠️  API error: {e}")
                click.echo(f"Retrying in {interval}s...")
                time.sleep(interval)
                continue

            now = datetime.now().strftime("%H:%M:%S")
            _render_display(
                mode=mode,
                availability=availability,
                phase="done",
                timestamp=now,
                interval=interval,
                progress_state=progress_state,
            )

            time.sleep(interval)

    except KeyboardInterrupt:
        click.echo("\nStopped watching.")
        sys.exit(0)
    finally:
        api_logger.setLevel(original_level)


_SECTION_TABLE_WIDTH = 108


@dataclass
class CPUResourceSummary:
    group_id: str
    group_name: str
    cpu_per_node_min: int | None = None
    cpu_per_node_max: int | None = None
    spec_cpu_min: int | None = None
    spec_cpu_max: int | None = None
    spec_memory_gib_min: int | None = None
    spec_memory_gib_max: int | None = None
    total_nodes: int = 0
    ready_nodes: int = 0
    free_nodes: int = 0
    has_cpu_specs: bool = False
    workspace_ids: list[str] = field(default_factory=list)
    workspace_aliases: list[str] = field(default_factory=list)


def _dedupe_ordered(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _workspace_aliases_by_id(config: Config | None) -> dict[str, list[str]]:
    aliases_by_id: dict[str, list[str]] = {}
    if config is None:
        return aliases_by_id
    for alias, raw_workspace_id in (getattr(config, "workspaces", None) or {}).items():
        workspace_id = str(raw_workspace_id or "").strip()
        if not workspace_id:
            continue
        aliases_by_id.setdefault(workspace_id, [])
        if alias not in aliases_by_id[workspace_id]:
            aliases_by_id[workspace_id].append(alias)
    return aliases_by_id


def _enumerate_accessible_workspace_ids(session) -> list[str]:  # noqa: ANN001
    candidates = [str(getattr(session, "workspace_id", "") or "").strip()]
    candidates.extend(
        str(ws or "").strip() for ws in (getattr(session, "all_workspace_ids", []) or [])
    )
    try:
        from inspire.platform.web.browser_api.workspaces import try_enumerate_workspaces

        for workspace in try_enumerate_workspaces(session, workspace_id=session.workspace_id):
            workspace_id = str(workspace.get("id") or "").strip()
            if workspace_id:
                candidates.append(workspace_id)
    except Exception:
        pass
    return [
        workspace_id
        for workspace_id in _dedupe_ordered(candidates)
        if workspace_id != _ZERO_WORKSPACE_ID
    ]


def _resolve_resources_workspace_scope(
    *,
    config: Config | None,
    show_all: bool,
    all_workspaces: bool = False,
) -> tuple[list[str], dict[str, list[str]], str]:
    aliases_by_id = _workspace_aliases_by_id(config)
    session = get_web_session(require_workspace=True)
    configured_workspace_ids = _dedupe_ordered(
        list((getattr(config, "workspaces", None) or {}).values()) if config else []
    )

    workspace_ids = list(configured_workspace_ids)
    if show_all:
        has_explicit_defaults = any(
            str(getattr(config, attr, "") or "").strip()
            for attr in ("workspace_cpu_id", "workspace_gpu_id", "workspace_internet_id")
        ) or bool(str(getattr(config, "job_workspace_id", "") or "").strip() if config else "")
        if has_explicit_defaults:
            workspace_ids = _dedupe_ordered(
                workspace_ids
                + [
                    (
                        str(getattr(config, "job_workspace_id", "") or "").strip()
                        if config is not None
                        else ""
                    ),
                    str(getattr(session, "workspace_id", "") or "").strip(),
                ]
            )
        else:
            workspace_ids = _enumerate_accessible_workspace_ids(session)
    if all_workspaces:
        workspace_ids = _dedupe_ordered(
            workspace_ids + _enumerate_accessible_workspace_ids(session)
        )

    if workspace_ids:
        scope_note = (
            "Shows all accessible workspaces"
            if all_workspaces
            else "Shows configured account workspaces only"
        )
        if show_all and not all_workspaces:
            scope_note = "Shows configured workspaces plus active/default session scope"
        return workspace_ids, aliases_by_id, scope_note

    fallback_workspace_id = str(getattr(session, "workspace_id", "") or "").strip()
    if not fallback_workspace_id or fallback_workspace_id == _ZERO_WORKSPACE_ID:
        return [], aliases_by_id, "No configured workspaces found"
    return (
        [fallback_workspace_id],
        aliases_by_id,
        "Shows current session workspace only (run 'inspire init --discover' to configure account workspaces)",
    )


def _apply_workspace_aliases(availability: list, aliases_by_id: dict[str, list[str]]) -> None:
    for entry in availability:
        entry.workspace_aliases = []
        for workspace_id in getattr(entry, "workspace_ids", []) or []:
            for alias in aliases_by_id.get(workspace_id, []):
                if alias not in entry.workspace_aliases:
                    entry.workspace_aliases.append(alias)


def _display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def _truncate_display(value: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _display_width(value) <= max_width:
        return value
    ellipsis = "…"
    ellipsis_width = _display_width(ellipsis)
    trimmed = ""
    for char in value:
        char_width = _display_width(char)
        if _display_width(trimmed) + char_width + ellipsis_width > max_width:
            return trimmed + ellipsis
        trimmed += char
    return trimmed


def _pad_display(value: str, width: int, align: str = "left") -> str:
    actual_width = _display_width(value)
    padding = max(width - actual_width, 0)
    if align == "right":
        return f"{' ' * padding}{value}"
    return f"{value}{' ' * padding}"


def _render_table(
    columns: list[tuple[str, str]],
    rows: list[dict[str, str]],
    *,
    min_width: int = 0,
) -> list[str]:
    widths: list[int] = []
    for index, (header, _align) in enumerate(columns):
        cell_width = _display_width(header)
        for row in rows:
            values = list(row.values())
            if index < len(values):
                cell_width = max(cell_width, _display_width(values[index]))
        widths.append(cell_width)

    rendered: list[str] = []
    header_cells = []
    for (header, align), width in zip(columns, widths):
        header_cells.append(_pad_display(header, width, "right" if align == "right" else "left"))
    header_line = "  ".join(header_cells)
    border_width = max(_display_width(header_line), min_width)
    rendered.append("─" * border_width)
    rendered.append(header_line)
    rendered.append("─" * border_width)
    for row in rows:
        row_cells = []
        values = list(row.values())
        for (_header, align), width, value in zip(columns, widths, values):
            row_cells.append(_pad_display(value, width, align))
        rendered.append("  ".join(row_cells))
    rendered.append("─" * border_width)
    return rendered


def _short_group_id(group_id: str) -> str:
    return group_id.split("-")[-1][-8:]


def _disambiguated_names(entries: list[object]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for entry in entries:
        name = str(getattr(entry, "group_name", "") or "")
        counts[name] = counts.get(name, 0) + 1

    display_names: dict[str, str] = {}
    for entry in entries:
        group_id = str(getattr(entry, "group_id", "") or "")
        name = str(getattr(entry, "group_name", "") or "")
        if counts.get(name, 0) > 1 and group_id:
            display_names[group_id] = f"{name} [{_short_group_id(group_id)}]"
        else:
            display_names[group_id] = name
    return display_names


def _format_range(min_value: int | None, max_value: int | None, *, none_value: str = "none") -> str:
    if min_value is None or max_value is None:
        return none_value
    if min_value == max_value:
        return str(min_value)
    return f"{min_value}-{max_value}"


def _update_range(
    current_min: int | None,
    current_max: int | None,
    candidate: int | None,
) -> tuple[int | None, int | None]:
    if candidate is None:
        return current_min, current_max
    if current_min is None or candidate < current_min:
        current_min = candidate
    if current_max is None or candidate > current_max:
        current_max = candidate
    return current_min, current_max


def _group_base_url(config: Config | None) -> str:
    base_url = getattr(config, "base_url", None) if config is not None else None
    return base_url or os.environ.get("INSPIRE_BASE_URL", "https://api.example.com").strip()


def _is_node_free(node: dict) -> bool:
    return (
        not (node.get("task_list") or [])
        and not str(node.get("cordon_type") or "").strip()
        and not node.get("is_maint", False)
        and str(node.get("resource_pool") or "").lower() != "fault"
    )


def _collect_cpu_node_summaries(
    *,
    config: Config | None,
    workspace_ids: list[str],
    aliases_by_id: dict[str, list[str]],
) -> dict[str, CPUResourceSummary]:
    session = get_web_session(require_workspace=True)
    base_url = _group_base_url(config)
    summaries: dict[str, CPUResourceSummary] = {}

    for workspace_id in workspace_ids:
        try:
            nodes = fetch_workspace_availability(
                session,
                base_url=base_url,
                workspace_id=workspace_id,
            )
        except Exception:
            continue
        for node in nodes:
            if int(node.get("gpu_count", 0) or 0) != 0:
                continue

            group_id = str(node.get("logic_compute_group_id") or "").strip()
            group_name = str(node.get("logic_compute_group_name") or "").strip()
            if not group_id or not group_name:
                continue

            summary = summaries.setdefault(
                group_id,
                CPUResourceSummary(group_id=group_id, group_name=group_name),
            )
            if workspace_id not in summary.workspace_ids:
                summary.workspace_ids.append(workspace_id)
            for alias in aliases_by_id.get(workspace_id, []):
                if alias not in summary.workspace_aliases:
                    summary.workspace_aliases.append(alias)

            cpu_count = node.get("cpu_count")
            cpu_value = int(cpu_count or 0) if cpu_count is not None else None
            summary.cpu_per_node_min, summary.cpu_per_node_max = _update_range(
                summary.cpu_per_node_min,
                summary.cpu_per_node_max,
                cpu_value,
            )
            summary.total_nodes += 1

            if str(node.get("status") or "").upper() == "READY":
                summary.ready_nodes += 1
                if _is_node_free(node):
                    summary.free_nodes += 1

    return summaries


def _collect_cpu_spec_summaries(
    *,
    workspace_ids: list[str],
    aliases_by_id: dict[str, list[str]],
) -> dict[str, CPUResourceSummary]:
    summaries: dict[str, CPUResourceSummary] = {}
    seen_workspace_group_pairs: set[tuple[str, str]] = set()

    for workspace_id in workspace_ids:
        try:
            groups = browser_api_module.list_notebook_compute_groups(workspace_id=workspace_id)
        except Exception:
            continue
        for group in groups:
            group_id = str(group.get("logic_compute_group_id") or group.get("id") or "").strip()
            group_name = str(group.get("name") or "").strip()
            if not group_id or not group_name:
                continue
            if (workspace_id, group_id) in seen_workspace_group_pairs:
                continue
            seen_workspace_group_pairs.add((workspace_id, group_id))

            try:
                prices = browser_api_module.get_resource_prices(
                    workspace_id=workspace_id,
                    logic_compute_group_id=group_id,
                )
            except Exception:
                continue
            cpu_rows = [row for row in prices if int(row.get("gpu_count", 0) or 0) == 0]
            if not cpu_rows:
                continue

            summary = summaries.setdefault(
                group_id,
                CPUResourceSummary(group_id=group_id, group_name=group_name),
            )
            if workspace_id not in summary.workspace_ids:
                summary.workspace_ids.append(workspace_id)
            for alias in aliases_by_id.get(workspace_id, []):
                if alias not in summary.workspace_aliases:
                    summary.workspace_aliases.append(alias)

            summary.has_cpu_specs = True
            for row in cpu_rows:
                cpu_count = row.get("cpu_count")
                cpu_value = int(cpu_count or 0) if cpu_count is not None else None
                memory_gib = row.get("memory_size_gib")
                memory_value = int(memory_gib or 0) if memory_gib is not None else None
                summary.spec_cpu_min, summary.spec_cpu_max = _update_range(
                    summary.spec_cpu_min,
                    summary.spec_cpu_max,
                    cpu_value,
                )
                summary.spec_memory_gib_min, summary.spec_memory_gib_max = _update_range(
                    summary.spec_memory_gib_min,
                    summary.spec_memory_gib_max,
                    memory_value,
                )

    return summaries


def _merge_cpu_resources(
    live_cpu: dict[str, CPUResourceSummary],
    spec_cpu: dict[str, CPUResourceSummary],
) -> list[CPUResourceSummary]:
    merged: dict[str, CPUResourceSummary] = {}
    for source in (live_cpu, spec_cpu):
        for group_id, entry in source.items():
            target = merged.setdefault(
                group_id,
                CPUResourceSummary(group_id=entry.group_id, group_name=entry.group_name),
            )
            if entry.group_name and not target.group_name:
                target.group_name = entry.group_name
            target.cpu_per_node_min, target.cpu_per_node_max = _update_range(
                target.cpu_per_node_min,
                target.cpu_per_node_max,
                entry.cpu_per_node_min,
            )
            target.cpu_per_node_min, target.cpu_per_node_max = _update_range(
                target.cpu_per_node_min,
                target.cpu_per_node_max,
                entry.cpu_per_node_max,
            )
            target.spec_cpu_min, target.spec_cpu_max = _update_range(
                target.spec_cpu_min,
                target.spec_cpu_max,
                entry.spec_cpu_min,
            )
            target.spec_cpu_min, target.spec_cpu_max = _update_range(
                target.spec_cpu_min,
                target.spec_cpu_max,
                entry.spec_cpu_max,
            )
            target.spec_memory_gib_min, target.spec_memory_gib_max = _update_range(
                target.spec_memory_gib_min,
                target.spec_memory_gib_max,
                entry.spec_memory_gib_min,
            )
            target.spec_memory_gib_min, target.spec_memory_gib_max = _update_range(
                target.spec_memory_gib_min,
                target.spec_memory_gib_max,
                entry.spec_memory_gib_max,
            )
            target.total_nodes += entry.total_nodes
            target.ready_nodes += entry.ready_nodes
            target.free_nodes += entry.free_nodes
            target.has_cpu_specs = target.has_cpu_specs or entry.has_cpu_specs
            for workspace_id in entry.workspace_ids:
                if workspace_id not in target.workspace_ids:
                    target.workspace_ids.append(workspace_id)
            for alias in entry.workspace_aliases:
                if alias not in target.workspace_aliases:
                    target.workspace_aliases.append(alias)

    return sorted(
        merged.values(),
        key=lambda item: (item.free_nodes, item.ready_nodes, item.total_nodes, item.group_name),
        reverse=True,
    )


def _collect_cpu_resources(
    *,
    config: Config | None,
    workspace_ids: list[str],
    aliases_by_id: dict[str, list[str]],
) -> list[CPUResourceSummary]:
    live_cpu = _collect_cpu_node_summaries(
        config=config,
        workspace_ids=workspace_ids,
        aliases_by_id=aliases_by_id,
    )
    spec_cpu = _collect_cpu_spec_summaries(
        workspace_ids=workspace_ids,
        aliases_by_id=aliases_by_id,
    )
    return _merge_cpu_resources(live_cpu, spec_cpu)


def _cpu_resources_to_json(cpu_resources: list[CPUResourceSummary]) -> list[dict]:
    return [
        {
            "group_id": item.group_id,
            "group_name": item.group_name,
            "workspace_ids": item.workspace_ids,
            "workspace_aliases": item.workspace_aliases,
            "cpu_per_node_min": item.cpu_per_node_min,
            "cpu_per_node_max": item.cpu_per_node_max,
            "spec_cpu_min": item.spec_cpu_min,
            "spec_cpu_max": item.spec_cpu_max,
            "spec_memory_gib_min": item.spec_memory_gib_min,
            "spec_memory_gib_max": item.spec_memory_gib_max,
            "total_nodes": item.total_nodes,
            "ready_nodes": item.ready_nodes,
            "free_nodes": item.free_nodes,
            "has_cpu_specs": item.has_cpu_specs,
        }
        for item in cpu_resources
    ]


def _render_gpu_summary_section(availability: list) -> list[str]:
    display_names = _disambiguated_names(availability)
    rows: list[dict[str, str]] = []
    total_available = 0
    total_used = 0
    total_low_pri = 0
    total_gpus = 0

    for item in sorted(availability, key=lambda value: value.available_gpus, reverse=True):
        total_available += item.available_gpus
        total_used += item.used_gpus
        total_low_pri += item.low_priority_gpus
        total_gpus += item.total_gpus
        rows.append(
            {
                "GPU Type": _truncate_display(str(item.gpu_type), 22),
                "Compute Group": _truncate_display(display_names[item.group_id], 28),
                "Available": str(item.available_gpus),
                "Used": str(item.used_gpus),
                "Low Pri": str(item.low_priority_gpus),
                "Total": str(item.total_gpus),
            }
        )

    lines = ["GPU Availability"]
    lines.extend(
        _render_table(
            [
                ("GPU Type", "left"),
                ("Compute Group", "left"),
                ("Available", "right"),
                ("Used", "right"),
                ("Low Pri", "right"),
                ("Total", "right"),
            ],
            rows,
            min_width=_SECTION_TABLE_WIDTH,
        )
    )
    lines.append(
        _pad_display("TOTAL", _display_width("GPU Type"), "left")
        + "  "
        + _pad_display("", max(_display_width("Compute Group"), 28), "left")
        + "  "
        + _pad_display(str(total_available), max(_display_width("Available"), 9), "right")
        + "  "
        + _pad_display(str(total_used), max(_display_width("Used"), 4), "right")
        + "  "
        + _pad_display(str(total_low_pri), max(_display_width("Low Pri"), 7), "right")
        + "  "
        + _pad_display(str(total_gpus), max(_display_width("Total"), 5), "right")
    )
    lines.append("")
    lines.append("Legend: Available = ready to use; Low Pri = preemptible usage")
    return lines


def _collect_accurate_availability_compat(
    *,
    config: Config | None,
    session,
    workspace_ids: list[str],
    aliases_by_id: dict[str, list[str]],
) -> list:
    try:
        availability = browser_api_module.get_accurate_gpu_availability(
            workspace_ids=workspace_ids,
        )
        _apply_workspace_aliases(availability, aliases_by_id)
        for item in availability:
            workspace_list = list(getattr(item, "workspace_ids", []) or [])
            workspace_id = workspace_list[0] if workspace_list else ""
            setattr(item, "workspace_id", workspace_id)
            setattr(item, "workspace_name", _workspace_label(config, session, workspace_id))
        return availability
    except TypeError:
        availability: list = []
        for workspace_id in workspace_ids:
            scoped = browser_api_module.get_accurate_gpu_availability(
                workspace_id=workspace_id,
                session=session,
            )
            workspace_name = _workspace_label(config, session, workspace_id)
            for item in scoped:
                setattr(item, "workspace_id", workspace_id)
                setattr(item, "workspace_name", workspace_name)
                setattr(item, "workspace_ids", [workspace_id])
                workspace_aliases = list(getattr(item, "workspace_aliases", []) or [])
                for alias in aliases_by_id.get(workspace_id, []):
                    if alias not in workspace_aliases:
                        workspace_aliases.append(alias)
                setattr(item, "workspace_aliases", workspace_aliases)
                availability.append(item)
        return availability


def _cpu_resources_as_availability(
    cpu_resources: list[CPUResourceSummary],
    *,
    config: Config | None,
    session,
) -> list:
    converted: list = []
    for item in cpu_resources:
        workspace_id = item.workspace_ids[0] if item.workspace_ids else ""
        workspace_name = _workspace_label(config, session, workspace_id) if workspace_id else ""
        total = item.cpu_per_node_max or item.spec_cpu_max or 0
        free = item.cpu_per_node_min or item.spec_cpu_min or 0
        entry = browser_api_module.GPUAvailability(
            group_id=item.group_id,
            group_name=item.group_name,
            gpu_type="CPU",
            total_gpus=total,
            used_gpus=max(total - free, 0),
            available_gpus=free,
            low_priority_gpus=0,
            workspace_ids=list(item.workspace_ids),
        )
        setattr(entry, "workspace_id", workspace_id)
        setattr(entry, "workspace_name", workspace_name)
        setattr(entry, "workspace_aliases", list(item.workspace_aliases))
        converted.append(entry)
    return converted


def _list_accurate_resources(ctx: Context, show_all: bool) -> None:
    try:
        config = _load_optional_config()
        session = get_web_session()
        workspace_ids, aliases_by_id, scope_note = _resolve_resources_workspace_scope(
            config=config,
            show_all=show_all,
            all_workspaces=False,
        )
        raw_availability = _collect_accurate_availability_compat(
            config=config,
            session=session,
            workspace_ids=workspace_ids,
            aliases_by_id=aliases_by_id,
        )
        availability = [item for item in raw_availability if getattr(item, "total_gpus", 0) > 0]
        cpu_resources = _collect_cpu_resources(
            config=config,
            workspace_ids=workspace_ids,
            aliases_by_id=aliases_by_id,
        )

        if ctx.json_output:
            output = [
                {
                    "group_id": a.group_id,
                    "group_name": a.group_name,
                    "gpu_type": a.gpu_type,
                    "total_gpus": a.total_gpus,
                    "used_gpus": a.used_gpus,
                    "available_gpus": a.available_gpus,
                    "low_priority_gpus": a.low_priority_gpus,
                    "workspace_id": getattr(a, "workspace_id", ""),
                    "workspace_name": getattr(a, "workspace_name", ""),
                    "workspace_ids": getattr(a, "workspace_ids", []),
                    "workspace_aliases": getattr(a, "workspace_aliases", []),
                }
                for a in availability
            ]
            click.echo(
                json_formatter.format_json(
                    {
                        "availability": output,
                        "cpu_resources": _cpu_resources_to_json(cpu_resources),
                    }
                )
            )
            return

        render_availability = list(raw_availability)
        if not any(
            str(getattr(item, "gpu_type", "") or "").upper() == "CPU" for item in raw_availability
        ):
            render_availability.extend(
                _cpu_resources_as_availability(
                    cpu_resources,
                    config=config,
                    session=session,
                )
            )
        has_browser_auth = bool(getattr(session, "cookies", None)) or bool(
            (getattr(session, "storage_state", None) or {}).get("cookies")
        )
        if has_browser_auth:
            _format_accurate_availability_table(render_availability, config=config, session=session)
        else:
            _format_accurate_availability_table(render_availability, config=config, session=None)
    except (SessionExpiredError, ValueError) as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


def _list_workspace_resources(ctx: Context, show_all: bool, no_cache: bool) -> None:
    try:
        if no_cache:
            clear_availability_cache()

        config = _load_optional_config()
        workspace_ids, aliases_by_id, _scope_note = _resolve_resources_workspace_scope(
            config=config,
            show_all=show_all,
            all_workspaces=False,
        )
        availability = fetch_resource_availability(
            config=config,
            known_only=False,
            workspace_ids=workspace_ids,
            workspace_aliases_by_id=aliases_by_id,
        )
        cpu_resources = _collect_cpu_resources(
            config=config,
            workspace_ids=workspace_ids,
            aliases_by_id=aliases_by_id,
        )

        if ctx.json_output:
            output = [
                {
                    "group_id": a.group_id,
                    "group_name": a.group_name,
                    "gpu_type": a.gpu_type,
                    "gpus_per_node": a.gpu_per_node,
                    "total_nodes": a.total_nodes,
                    "ready_nodes": a.ready_nodes,
                    "free_nodes": a.free_nodes,
                    "free_gpus": a.free_gpus,
                    "workspace_id": (getattr(a, "workspace_ids", []) or [""])[0],
                    "workspace_ids": getattr(a, "workspace_ids", []),
                    "workspace_aliases": getattr(a, "workspace_aliases", []),
                }
                for a in availability
            ]
            click.echo(
                json_formatter.format_json(
                    {
                        "availability": output,
                        "cpu_resources": _cpu_resources_to_json(cpu_resources),
                    }
                )
            )
            return

        _format_availability_table(availability, workspace_mode=True, config=config)
    except (SessionExpiredError, ValueError) as e:
        _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
    except Exception as e:
        _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)


def run_resources_list(
    ctx: Context,
    *,
    no_cache: bool,
    show_all: bool,
    all_workspaces: bool,
    watch: bool,
    interval: int,
    view: str,
    use_global: bool,
) -> None:
    if use_global:
        click.echo("Note: --global is deprecated; use --view nodes instead.", err=True)
        if view == "summary":
            view = "nodes"

    if watch:
        if ctx.json_output:
            click.echo(
                json_formatter.format_json_error(
                    "InvalidOption",
                    "Watch mode not supported with JSON output",
                    EXIT_CONFIG_ERROR,
                ),
                err=True,
            )
            sys.exit(EXIT_CONFIG_ERROR)

        _watch_resources(
            ctx,
            show_all or all_workspaces,
            show_all or all_workspaces,
            interval,
            view == "nodes",
            use_global,
        )
        return

    if view == "nodes":
        if all_workspaces:
            _list_workspace_resources(ctx, True, no_cache)
        else:
            _list_workspace_resources(ctx, show_all, no_cache)
        return

    if all_workspaces:
        config = _load_optional_config()
        session = get_web_session()
        workspace_ids, aliases_by_id, _scope_note = _resolve_resources_workspace_scope(
            config=config,
            show_all=show_all,
            all_workspaces=True,
        )
        try:
            availability = _collect_accurate_availability_compat(
                config=config,
                session=session,
                workspace_ids=workspace_ids,
                aliases_by_id=aliases_by_id,
            )
            availability = [item for item in availability if getattr(item, "total_gpus", 0) > 0]
            cpu_resources = _collect_cpu_resources(
                config=config,
                workspace_ids=workspace_ids,
                aliases_by_id=aliases_by_id,
            )
            if ctx.json_output:
                output = [
                    {
                        "group_id": a.group_id,
                        "group_name": a.group_name,
                        "gpu_type": a.gpu_type,
                        "total_gpus": a.total_gpus,
                        "used_gpus": a.used_gpus,
                        "available_gpus": a.available_gpus,
                        "low_priority_gpus": a.low_priority_gpus,
                        "workspace_id": getattr(a, "workspace_id", ""),
                        "workspace_name": getattr(a, "workspace_name", ""),
                        "workspace_ids": getattr(a, "workspace_ids", []),
                        "workspace_aliases": getattr(a, "workspace_aliases", []),
                    }
                    for a in availability
                ]
                click.echo(
                    json_formatter.format_json(
                        {
                            "availability": output,
                            "cpu_resources": _cpu_resources_to_json(cpu_resources),
                        }
                    )
                )
                return
            render_availability = availability + _cpu_resources_as_availability(
                cpu_resources,
                config=config,
                session=session,
            )
            _format_accurate_availability_table(
                render_availability,
                config=config,
                session=session,
            )
            return
        except (SessionExpiredError, ValueError) as e:
            _handle_error(ctx, "AuthenticationError", str(e), EXIT_AUTH_ERROR)
            return
        except Exception as e:
            _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
            return

    _list_accurate_resources(ctx, show_all)


@click.command("list")
@click.option(
    "--no-cache",
    is_flag=True,
    help="Bypass cached node availability (workspace view only)",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Expand scope to all accessible workspaces (default: configured account workspaces)",
)
@click.option(
    "--all-workspaces",
    is_flag=True,
    help="Include discovered session workspaces in addition to configured/default scope",
)
@click.option(
    "--watch",
    "-w",
    is_flag=True,
    help="Continuously watch availability (refreshes every 30s)",
)
@click.option(
    "--interval",
    "-i",
    type=int,
    default=30,
    help="Watch refresh interval in seconds (default: 30)",
)
@click.option(
    "--view",
    type=click.Choice(["summary", "nodes"]),
    default="summary",
    show_default=True,
    help="Display summary or node-level availability",
)
@click.option(
    "--global",
    "use_global",
    is_flag=True,
    help="Deprecated: alias for --view nodes",
)
@pass_context
def list_resources(
    ctx: Context,
    no_cache: bool,
    show_all: bool,
    all_workspaces: bool,
    watch: bool,
    interval: int,
    view: str,
    use_global: bool = False,
) -> None:
    """List GPU availability across compute groups."""
    run_resources_list(
        ctx,
        no_cache=no_cache,
        show_all=show_all,
        all_workspaces=all_workspaces,
        watch=watch,
        interval=interval,
        view=view,
        use_global=use_global,
    )
