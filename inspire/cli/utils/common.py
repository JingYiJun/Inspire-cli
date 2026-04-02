"""Common CLI decorators and utilities."""

from __future__ import annotations

from typing import Any, Callable

import click


def json_option(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that adds --json flag option to a Click command.

    Use this instead of manually adding @click.option for --json.
    The decorated function will receive json_output parameter.

    Usage:
        from inspire.cli.utils.common import json_option

        @click.command()
        @json_option
        @pass_context
        def my_command(ctx, json_output, ...):
            json_output = resolve_json_output(ctx, json_output)
            ...
    """
    return click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Alias for global --json",
    )(func)
