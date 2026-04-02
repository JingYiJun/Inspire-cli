"""Project management commands.

Usage:
    inspire project list
    inspire project select
"""

from __future__ import annotations

import click

from .project_commands import list_projects_cmd
from .select import select_projects


@click.group()
def project():
    """View project information and GPU quota.

    \b
    Examples:
        inspire project list          # Show project quota table
        inspire project list --json   # JSON output with all fields
        inspire project select        # Interactive project priority selector
    """
    pass


project.add_command(list_projects_cmd)
project.add_command(select_projects)
