"""Discovery integration for wizard mode."""

from __future__ import annotations

from typing import Any

import click


def _coerce_project_field(project: Any, field_name: str) -> Any:
    if isinstance(project, dict):
        return project.get(field_name)
    return getattr(project, field_name, None)


def run_discovery_for_wizard(
    username: str,
    password: str,
    base_url: str,
) -> dict[str, Any] | None:
    """Run discovery and return results for wizard.

    This is a simplified version of the discovery process that returns
    data instead of writing directly to config files.

    Args:
        username: Platform username
        password: Platform password
        base_url: Platform base URL

    Returns:
        Dictionary with discovered data or None if discovery failed
    """
    try:
        from inspire.platform.web import browser_api as browser_api_module
        from inspire.platform.web import session as web_session_module
        from inspire.platform.web.browser_api.workspaces import try_enumerate_workspaces
        from inspire.platform.web.browser_api.core import _set_base_url
        from inspire.platform.web.session import DEFAULT_WORKSPACE_ID
        from .wizard import _format_project_label

        # Import browser API modules

        # Initialize results
        results = {
            "workspaces": {},
            "projects": [],
            "compute_groups": [],
            "success": False,
        }
        discovered_workspace_ids: list[str] = []

        click.echo("\n🔍 Discovering your Inspire platform account...")

        # Set up web session
        try:
            _set_base_url(base_url)
            session = web_session_module.login_with_playwright(
                username,
                password,
                base_url=base_url,
            )
            click.echo("✓ Authenticated successfully")
        except Exception as e:
            click.echo(f"✗ Authentication failed: {e}")
            return None

        # Discover workspaces
        click.echo("\n📋 Discovering workspaces...")
        try:
            # Get available workspaces
            workspace_list = try_enumerate_workspaces(session, workspace_id=session.workspace_id)
            discovered_workspace_ids = [
                str(ws.get("id") or "").strip()
                for ws in workspace_list
                if isinstance(ws, dict) and str(ws.get("id") or "").strip()
            ]

            # Try to identify standard workspaces by name patterns
            for ws in workspace_list:
                ws_id = ws.get("id", "")
                ws_name = ws.get("name", "").lower()

                # Heuristic: identify common workspace types
                if "cpu" in ws_name or "default" in ws_name:
                    results["workspaces"]["cpu"] = ws_id
                elif "gpu" in ws_name and "4090" not in ws_name:
                    results["workspaces"]["gpu"] = ws_id
                elif "4090" in ws_name or "internet" in ws_name:
                    results["workspaces"]["internet"] = ws_id
                else:
                    # Store with slugified name
                    from inspire.cli.commands.init.discover import _slugify_alias

                    alias = _slugify_alias(ws_name)
                    if alias:
                        results["workspaces"][alias] = ws_id

            click.echo(f"✓ Found {len(workspace_list)} workspace(s)")
            for alias, ws_id in results["workspaces"].items():
                click.echo(f"  - {alias}: {ws_id[:20]}...")

        except Exception as e:
            click.echo(f"⚠ Could not discover workspaces: {e}")

        # Discover projects
        click.echo("\n📁 Discovering projects...")
        try:
            # Use default workspace for project discovery
            workspace_id = (
                results["workspaces"].get("cpu")
                or results["workspaces"].get("gpu")
                or DEFAULT_WORKSPACE_ID
            )

            projects_data = browser_api_module.list_projects(
                workspace_id=workspace_id,
                session=session,
            )

            for proj in projects_data:
                if isinstance(proj, dict):
                    project_id = str(proj.get("id", "")).strip()
                    name = str(proj.get("name", "Unknown")).strip() or "Unknown"
                    quota_data = proj.get("quota", {})
                    gpu_used = quota_data.get("used", 0)
                    gpu_total = quota_data.get("total", 0)
                else:
                    project_id = str(
                        getattr(proj, "project_id", "") or getattr(proj, "id", "")
                    ).strip()
                    name = str(getattr(proj, "name", "Unknown")).strip() or "Unknown"
                    gpu_used = 0
                    gpu_total = 0

                project_info = {
                    "id": project_id,
                    "name": name,
                    "quota": {"gpu_used": gpu_used, "gpu_total": gpu_total},
                    "budget": _coerce_project_field(proj, "budget"),
                    "remain_budget": _coerce_project_field(proj, "remain_budget"),
                    "member_remain_budget": _coerce_project_field(proj, "member_remain_budget"),
                    "member_remain_gpu_hours": _coerce_project_field(
                        proj, "member_remain_gpu_hours"
                    ),
                    "gpu_limit": bool(_coerce_project_field(proj, "gpu_limit")),
                    "member_gpu_limit": bool(_coerce_project_field(proj, "member_gpu_limit")),
                    "priority_level": str(_coerce_project_field(proj, "priority_level") or ""),
                    "priority_name": str(_coerce_project_field(proj, "priority_name") or ""),
                }
                results["projects"].append(project_info)

            click.echo(f"✓ Found {len(results['projects'])} project(s)")
            for proj in results["projects"][:5]:  # Show first 5
                click.echo(f"  - {_format_project_label(proj)}")

            if len(results["projects"]) > 5:
                click.echo(f"  ... and {len(results['projects']) - 5} more")

        except Exception as e:
            click.echo(f"⚠ Could not discover projects: {e}")

        # Discover compute groups
        click.echo("\n⚙️  Discovering compute groups...")
        try:
            from inspire.cli.commands.init.discover import (
                _correct_workspace_aliases,
                _discover_compute_groups,
                _merge_compute_groups,
            )

            workspace_ids_for_groups = discovered_workspace_ids or [workspace_id]
            merged_groups: list[dict[str, Any]] = []
            for group_workspace_id in workspace_ids_for_groups:
                groups = _discover_compute_groups(
                    browser_api_module=browser_api_module,
                    session=session,
                    workspace_id=group_workspace_id,
                )
                for cg in groups:
                    cg.setdefault("workspace_ids", [])
                    if group_workspace_id not in cg["workspace_ids"]:
                        cg["workspace_ids"].append(group_workspace_id)
                merged_groups = _merge_compute_groups(merged_groups, groups)

            results["compute_groups"] = merged_groups
            _correct_workspace_aliases(results["workspaces"], results["compute_groups"])

            click.echo(f"✓ Found {len(results['compute_groups'])} compute group(s)")
            for cg in results["compute_groups"][:5]:
                name = cg["name"]
                gpu = cg.get("gpu_type", "CPU")
                click.echo(f"  - {name} ({gpu})")

            if len(results["compute_groups"]) > 5:
                click.echo(f"  ... and {len(results['compute_groups']) - 5} more")

        except Exception as e:
            click.echo(f"⚠ Could not discover compute groups: {e}")

        results["success"] = True
        click.echo("\n✅ Discovery complete!")

        return results

    except ImportError as e:
        click.echo(f"✗ Discovery requires additional dependencies: {e}")
        click.echo("  Try: uv pip install playwright")
        return None
    except Exception as e:
        click.echo(f"✗ Discovery failed: {e}")
        return None
