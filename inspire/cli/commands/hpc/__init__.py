"""HPC commands for Inspire CLI."""

from __future__ import annotations

import click

from .hpc_create import create
from .hpc_commands import list_jobs, script, status, stop, wait


@click.group()
def hpc() -> None:
    """Manage HPC jobs."""


hpc.add_command(create)
hpc.add_command(status)
hpc.add_command(stop)
hpc.add_command(list_jobs)
hpc.add_command(wait)
hpc.add_command(script)


__all__ = ["hpc"]
