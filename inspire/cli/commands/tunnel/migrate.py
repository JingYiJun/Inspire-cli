"""Tunnel migrate command."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import click

from inspire.bridge.tunnel import BridgeProfile, load_tunnel_config, save_tunnel_config
from inspire.cli.context import Context, pass_context
from inspire.cli.formatters import json_formatter
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web import session as web_session_module

_BRIDGE_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass
class _MigrationSummary:
    renamed: list[tuple[str, str]]
    removed: list[str]
    updated: list[str]
    skipped: list[str]


def _normalize_identifier(value: object) -> str:
    return str(value or "").strip().lower()


def _sanitize_bridge_name(raw_name: object, *, fallback: str) -> str:
    name = str(raw_name or "").strip()
    if not name:
        return fallback
    sanitized = _BRIDGE_NAME_SANITIZE_RE.sub("-", name)
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-_")
    return sanitized or fallback


def _dedupe_aliases(*values: object) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            items = value
        else:
            items = [value]
        for item in items:
            token = str(item or "").strip()
            if not token:
                continue
            lowered = _normalize_identifier(token)
            if lowered in seen:
                continue
            seen.add(lowered)
            aliases.append(token)
    return aliases


def _is_legacy_notebook_bridge(bridge: BridgeProfile) -> bool:
    notebook_id = str(bridge.notebook_id or "").strip()
    if not notebook_id:
        return False
    return bridge.name == f"notebook-{notebook_id[:8]}"


def _resolve_notebook_name(
    *,
    notebook_id: str,
    bridges: list[BridgeProfile],
    session_holder: dict[str, object],
) -> Optional[str]:
    for bridge in bridges:
        notebook_name = str(getattr(bridge, "notebook_name", "") or "").strip()
        if notebook_name:
            return notebook_name

    session = session_holder.get("session")
    if session_holder.get("session_unavailable"):
        return None

    if session is None:
        try:
            session = web_session_module.get_web_session()
        except Exception:
            session_holder["session_unavailable"] = True
            return None
        session_holder["session"] = session

    try:
        detail = browser_api_module.get_notebook_detail(notebook_id=notebook_id, session=session)
    except Exception:
        return None

    notebook_name = str((detail or {}).get("name") or "").strip()
    return notebook_name or None


def _unique_name(
    desired_name: str,
    *,
    notebook_id: str,
    reserved_names: set[str],
) -> str:
    desired = str(desired_name or "").strip() or f"notebook-{notebook_id[:8]}"
    if _normalize_identifier(desired) not in reserved_names:
        reserved_names.add(_normalize_identifier(desired))
        return desired

    suffix = notebook_id[:8] or "bridge"
    candidate = f"{desired}-{suffix}"
    if _normalize_identifier(candidate) not in reserved_names:
        reserved_names.add(_normalize_identifier(candidate))
        return candidate

    index = 2
    while True:
        candidate = f"{desired}-{suffix}-{index}"
        lowered = _normalize_identifier(candidate)
        if lowered not in reserved_names:
            reserved_names.add(lowered)
            return candidate
        index += 1


def _choose_bridge_for_group(
    *,
    bridges: list[BridgeProfile],
    default_bridge: Optional[str],
) -> BridgeProfile:
    default = _normalize_identifier(default_bridge)
    for bridge in bridges:
        if _normalize_identifier(bridge.name) == default:
            return bridge
    non_legacy = [bridge for bridge in bridges if not _is_legacy_notebook_bridge(bridge)]
    if non_legacy:
        return sorted(non_legacy, key=lambda item: item.name)[0]
    return sorted(bridges, key=lambda item: item.name)[0]


def _migrate_tunnel_config(config) -> tuple[object, _MigrationSummary]:
    session_holder: dict[str, object] = {"session": None, "session_unavailable": False}
    summary = _MigrationSummary(renamed=[], removed=[], updated=[], skipped=[])
    preserved: dict[str, BridgeProfile] = {}
    reserved_names: set[str] = set()

    notebook_groups: dict[str, list[BridgeProfile]] = {}
    for bridge in config.list_bridges():
        notebook_id = str(getattr(bridge, "notebook_id", "") or "").strip()
        if notebook_id:
            notebook_groups.setdefault(notebook_id, []).append(bridge)
            continue
        preserved[bridge.name] = bridge
        reserved_names.add(_normalize_identifier(bridge.name))

    new_default = config.default_bridge

    for notebook_id, bridges in notebook_groups.items():
        chosen = _choose_bridge_for_group(bridges=bridges, default_bridge=config.default_bridge)
        fallback_name = str(chosen.name or f"notebook-{notebook_id[:8]}")
        notebook_name = _resolve_notebook_name(
            notebook_id=notebook_id,
            bridges=bridges,
            session_holder=session_holder,
        )

        desired_name = chosen.name
        if _is_legacy_notebook_bridge(chosen) and notebook_name:
            desired_name = _sanitize_bridge_name(notebook_name, fallback=fallback_name)

        final_name = _unique_name(
            desired_name,
            notebook_id=notebook_id,
            reserved_names=reserved_names,
        )

        merged_aliases = _dedupe_aliases(
            [
                bridge.name
                for bridge in bridges
                if _normalize_identifier(bridge.name) != _normalize_identifier(final_name)
            ],
            *[bridge.aliases for bridge in bridges],
        )
        merged_aliases = [
            alias
            for alias in merged_aliases
            if _normalize_identifier(alias) != _normalize_identifier(final_name)
        ]

        updated_bridge = BridgeProfile(
            name=final_name,
            proxy_url=chosen.proxy_url,
            aliases=merged_aliases,
            ssh_user=chosen.ssh_user,
            ssh_port=chosen.ssh_port,
            has_internet=chosen.has_internet,
            notebook_id=chosen.notebook_id,
            notebook_name=notebook_name or chosen.notebook_name,
            rtunnel_port=chosen.rtunnel_port,
        )
        preserved[final_name] = updated_bridge

        original_names = [bridge.name for bridge in bridges]
        for bridge_name in original_names:
            if bridge_name == final_name:
                continue
            summary.removed.append(bridge_name)

        if chosen.name != final_name:
            summary.renamed.append((chosen.name, final_name))
        elif merged_aliases != list(chosen.aliases or []) or (
            notebook_name and notebook_name != str(chosen.notebook_name or "")
        ):
            summary.updated.append(final_name)

        if config.default_bridge in original_names:
            new_default = final_name

    config.bridges = preserved
    config.default_bridge = new_default if new_default in preserved else next(iter(preserved), None)
    return config, summary


@click.command("migrate")
@click.option("--dry-run", is_flag=True, help="Preview bridge migration without saving changes")
@pass_context
def tunnel_migrate(ctx: Context, dry_run: bool) -> None:
    """Migrate legacy notebook bridge names to stable notebook-based aliases."""
    config = load_tunnel_config()
    migrated, summary = _migrate_tunnel_config(config)

    payload = {
        "dry_run": dry_run,
        "default_bridge": migrated.default_bridge,
        "renamed": [{"from": old, "to": new} for old, new in summary.renamed],
        "removed": summary.removed,
        "updated": summary.updated,
        "skipped": summary.skipped,
        "bridges": [bridge.to_dict() for bridge in migrated.list_bridges()],
    }

    if ctx.json_output:
        click.echo(json_formatter.format_json(payload))
        return

    if not any([summary.renamed, summary.removed, summary.updated]):
        click.echo("No bridge migration changes needed.")
        return

    click.echo("Bridge migration plan:" if dry_run else "Bridge migration completed:")
    if summary.renamed:
        click.echo("Renamed:")
        for old, new in summary.renamed:
            click.echo(f"  {old} -> {new}")
    if summary.updated:
        click.echo("Updated:")
        for name in summary.updated:
            click.echo(f"  {name}")
    if summary.removed:
        click.echo("Removed duplicates:")
        for name in summary.removed:
            click.echo(f"  {name}")
    if migrated.default_bridge:
        click.echo(f"Default bridge: {migrated.default_bridge}")

    if not dry_run:
        save_tunnel_config(migrated)


__all__ = ["tunnel_migrate"]
