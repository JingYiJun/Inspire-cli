"""Tunnel update command."""

from __future__ import annotations


import click

from ._ssh_config_sync import sync_installed_ssh_config
from inspire.bridge.tunnel import load_tunnel_config, save_tunnel_config
from inspire.cli.context import Context, EXIT_CONFIG_ERROR, pass_context
from inspire.cli.formatters import json_formatter
from inspire.cli.utils.common import json_option
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.notebook_cli import resolve_json_output
from inspire.cli.utils.output import emit_info, emit_success, emit_warning


@click.command("update")
@click.argument("name")
@click.option("--url", help="Update the proxy URL")
@click.option("--ssh-user", help="Update the SSH user")
@click.option("--ssh-port", type=int, help="Update the SSH port")
@click.option(
    "--has-internet",
    is_flag=True,
    flag_value=True,
    default=None,
    help="Mark bridge as having internet access",
)
@click.option(
    "--no-internet",
    is_flag=True,
    flag_value=True,
    default=None,
    help="Mark bridge as having no internet access",
)
@json_option
@pass_context
def tunnel_update(
    ctx: Context,
    name: str,
    url: str,
    ssh_user: str,
    ssh_port: int,
    has_internet: bool,
    no_internet: bool,
    json_output: bool = False,
) -> None:
    json_output = resolve_json_output(ctx, json_output)
    """Update an existing bridge profile.

    \b
    Examples:
        inspire tunnel update mybridge --has-internet
        inspire tunnel update mybridge --no-internet
        inspire tunnel update mybridge --url "https://new-url.../proxy/31337/"
        inspire tunnel update mybridge --ssh-port 22223
    """
    config = load_tunnel_config()

    if name not in config.bridges:
        _handle_error(
            ctx,
            "NotFound",
            f"Bridge '{name}' not found",
            EXIT_CONFIG_ERROR,
        )

    if has_internet and no_internet:
        _handle_error(
            ctx,
            "ValidationError",
            "Cannot specify both --has-internet and --no-internet",
            EXIT_CONFIG_ERROR,
        )

    bridge = config.bridges[name]
    updated_fields: list[str] = []

    if url is not None:
        bridge.proxy_url = url
        updated_fields.append("url")
    if ssh_user is not None:
        bridge.ssh_user = ssh_user
        updated_fields.append("ssh_user")
    if ssh_port is not None:
        bridge.ssh_port = ssh_port
        updated_fields.append("ssh_port")
    if has_internet:
        bridge.has_internet = True
        updated_fields.append("has_internet")
    elif no_internet:
        bridge.has_internet = False
        updated_fields.append("has_internet")

    if not updated_fields:
        _handle_error(
            ctx,
            "ValidationError",
            "No fields to update. Use --url, --ssh-user, --ssh-port, --has-internet, or --no-internet.",
            EXIT_CONFIG_ERROR,
        )

    save_tunnel_config(config)
    ssh_synced, ssh_sync_error = sync_installed_ssh_config(config)

    if ctx.json_output:
        click.echo(
            json_formatter.format_json(
                {
                    "status": "updated",
                    "name": name,
                    "updated_fields": updated_fields,
                    "bridge": bridge.to_dict(),
                    "ssh_config_synced": ssh_synced,
                }
            )
        )
        return

    emit_success(
        ctx,
        payload={
            "status": "updated",
            "name": name,
            "updated_fields": updated_fields,
            "bridge": bridge.to_dict(),
            "ssh_config_synced": ssh_synced,
        },
        text=f"Updated bridge: {name}",
    )
    for field in updated_fields:
        if field == "url":
            emit_info(ctx, f"  URL: {bridge.proxy_url}")
        elif field == "ssh_user":
            emit_info(ctx, f"  SSH user: {bridge.ssh_user}")
        elif field == "ssh_port":
            emit_info(ctx, f"  SSH port: {bridge.ssh_port}")
        elif field == "has_internet":
            emit_info(ctx, f"  Internet: {'yes' if bridge.has_internet else 'no'}")
    if ssh_synced:
        emit_info(ctx, "  SSH config: synced")
    elif ssh_sync_error:
        emit_warning(ctx, f"  SSH config sync skipped: {ssh_sync_error}")
