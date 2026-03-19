"""Image subcommands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click

from inspire.cli.context import (
    Context,
    EXIT_API_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_VALIDATION_ERROR,
    pass_context,
)
from inspire.cli.formatters import human_formatter, json_formatter
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.id_resolver import (
    is_full_uuid,
    is_partial_id,
    normalize_partial,
    resolve_partial_id,
)
from inspire.cli.utils.notebook_cli import (
    load_config,
    require_web_session,
    resolve_json_output,
)
from inspire.config import ConfigError
from inspire.config.workspaces import select_workspace_id
from inspire.platform.web import browser_api as browser_api_module
from inspire.platform.web.session import DEFAULT_WORKSPACE_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOURCE_CHOICES = ("official", "public", "private", "personal-visible", "all")
_IMAGE_SEARCH_SOURCES = ("official", "public", "private", "personal-visible")


@dataclass
class _ImageLookupCandidate:
    image_id: str
    name: str
    url: str
    source: str
    status: str
    workspace_id: str
    workspace_name: str


def _image_to_dict(img: browser_api_module.CustomImageInfo) -> dict:
    """Convert a CustomImageInfo to a plain dict for JSON output."""
    return {
        "image_id": img.image_id,
        "url": img.url,
        "name": img.name,
        "framework": img.framework,
        "version": img.version,
        "source": img.source,
        "status": img.status,
        "description": img.description,
        "created_at": img.created_at,
    }


def _resolve_image_id(
    ctx: Context,
    image_id: str,
    json_output: bool,
    session,
) -> str:
    """Resolve a full or partial image ID.

    Full UUIDs pass through; partial hex triggers a list + prefix match.
    """
    image_id = image_id.strip()

    if is_full_uuid(image_id):
        return image_id

    if not is_partial_id(image_id):
        return image_id  # not hex — let the API handle the error

    partial = normalize_partial(image_id)

    try:
        all_images: list[browser_api_module.CustomImageInfo] = []
        for src_key in ("official", "public", "private", "personal-visible"):
            items = browser_api_module.list_images_by_source(source=src_key, session=session)
            all_images.extend(items)
    except Exception:
        return image_id  # can't list — pass through and let the API error

    matches: list[tuple[str, str]] = []
    seen: set[str] = set()
    for img in all_images:
        iid = img.image_id
        if iid in seen:
            continue
        seen.add(iid)
        if iid.lower().startswith(partial):
            label = img.name or img.status or ""
            matches.append((iid, label))

    if not matches:
        return image_id  # no match — pass through for API error

    return resolve_partial_id(ctx, partial, "image", matches, json_output)


def _append_unique_workspace_id(candidates: list[str], seen: set[str], workspace_id: Optional[str]) -> None:
    value = str(workspace_id or "").strip()
    if not value or value == DEFAULT_WORKSPACE_ID or value in seen:
        return
    seen.add(value)
    candidates.append(value)


def _workspace_label(config, session, workspace_id: str) -> str:
    all_names = getattr(session, "all_workspace_names", None) or {}
    name = str(all_names.get(workspace_id, "")).strip()
    if name:
        return name

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
            return alias

    return workspace_id


def _resolve_detail_workspace_search_order(
    config,
    session,
    *,
    workspace: Optional[str],
    workspace_id: Optional[str],
) -> tuple[list[str], list[str]]:
    if workspace_id:
        validated = select_workspace_id(config, explicit_workspace_id=workspace_id)
        return [validated], []

    if workspace:
        validated = select_workspace_id(config, explicit_workspace_name=workspace)
        return [validated], []

    primary: list[str] = []
    fallback: list[str] = []
    seen: set[str] = set()

    try:
        default_workspace_id = select_workspace_id(config)
    except ConfigError:
        default_workspace_id = None

    _append_unique_workspace_id(primary, seen, default_workspace_id)
    _append_unique_workspace_id(primary, seen, getattr(session, "workspace_id", None))

    for candidate in (
        getattr(config, "workspace_cpu_id", None),
        getattr(config, "workspace_gpu_id", None),
        getattr(config, "workspace_internet_id", None),
        getattr(config, "job_workspace_id", None),
    ):
        _append_unique_workspace_id(fallback, seen, candidate)

    for candidate in (getattr(config, "workspaces", None) or {}).values():
        _append_unique_workspace_id(fallback, seen, candidate)

    for candidate in getattr(session, "all_workspace_ids", None) or []:
        _append_unique_workspace_id(fallback, seen, candidate)

    return primary, fallback


def _collect_image_lookup_matches(
    *,
    session,
    config,
    workspace_ids: list[str],
    matcher,
) -> list[_ImageLookupCandidate]:
    matches_by_id: dict[str, _ImageLookupCandidate] = {}

    for ws_id in workspace_ids:
        ws_label = _workspace_label(config, session, ws_id)
        for source in _IMAGE_SEARCH_SOURCES:
            items = browser_api_module.list_images_by_source(
                source=source,
                workspace_id=ws_id,
                session=session,
            )
            for img in items:
                candidate = _ImageLookupCandidate(
                    image_id=img.image_id,
                    name=img.name,
                    url=img.url,
                    source=img.source,
                    status=img.status,
                    workspace_id=ws_id,
                    workspace_name=ws_label,
                )
                if not matcher(candidate):
                    continue
                matches_by_id.setdefault(candidate.image_id, candidate)

    return list(matches_by_id.values())


def _choose_image_match(
    ctx: Context,
    query: str,
    matches: list[_ImageLookupCandidate],
    *,
    json_output: bool,
    lookup_kind: str,
) -> str:
    if not matches:
        raise LookupError(f"No image matches {lookup_kind} '{query}'.")

    if len(matches) == 1:
        return matches[0].image_id

    if json_output:
        options = ", ".join(
            f"{item.image_id} ({item.name or item.url} @ {item.workspace_name})" for item in matches
        )
        _handle_error(
            ctx,
            "AmbiguousImage",
            f"{lookup_kind.capitalize()} '{query}' matches {len(matches)} images: {options}",
            EXIT_VALIDATION_ERROR,
            hint="Use --workspace/--workspace-id or provide a full image ID.",
        )
        raise click.Abort()

    click.echo(f"{lookup_kind.capitalize()} '{query}' matches {len(matches)} images:")
    for idx, item in enumerate(matches, start=1):
        label = item.name or item.url or item.image_id
        click.echo(
            f"  [{idx}] {item.image_id}  {label}  {item.source}  {item.workspace_name}"
        )

    choice = click.prompt(
        "Select image",
        type=click.IntRange(1, len(matches)),
        default=1,
        show_default=True,
    )
    return matches[choice - 1].image_id


def _resolve_image_ref_for_detail(
    ctx: Context,
    image_ref: str,
    *,
    json_output: bool,
    session,
    config,
    workspace: Optional[str],
    workspace_id: Optional[str],
) -> tuple[str, Optional[browser_api_module.CustomImageInfo]]:
    image_ref = image_ref.strip()

    if is_full_uuid(image_ref):
        return image_ref, None

    primary_workspace_ids, fallback_workspace_ids = _resolve_detail_workspace_search_order(
        config,
        session,
        workspace=workspace,
        workspace_id=workspace_id,
    )

    if is_partial_id(image_ref):
        partial = normalize_partial(image_ref)

        def partial_match(candidate: _ImageLookupCandidate) -> bool:
            return candidate.image_id.lower().startswith(partial)

        matches = _collect_image_lookup_matches(
            session=session,
            config=config,
            workspace_ids=primary_workspace_ids,
            matcher=partial_match,
        )
        if not matches and fallback_workspace_ids:
            matches = _collect_image_lookup_matches(
                session=session,
                config=config,
                workspace_ids=fallback_workspace_ids,
                matcher=partial_match,
            )

        resolved = _choose_image_match(
            ctx,
            partial,
            matches,
            json_output=json_output,
            lookup_kind="partial id",
        )
        return resolved, None

    try:
        image = browser_api_module.get_image_detail(image_id=image_ref, session=session)
        return image_ref, image
    except Exception:
        pass

    lowered_ref = image_ref.lower()

    def ref_match(candidate: _ImageLookupCandidate) -> bool:
        name = (candidate.name or "").strip().lower()
        url = (candidate.url or "").strip().lower()
        url_tail = url.rsplit("/", 1)[-1] if url else ""
        if lowered_ref == name or lowered_ref == url_tail:
            return True
        if "/" in lowered_ref and url.endswith(lowered_ref):
            return True
        return lowered_ref == url

    matches = _collect_image_lookup_matches(
        session=session,
        config=config,
        workspace_ids=primary_workspace_ids,
        matcher=ref_match,
    )
    if not matches and fallback_workspace_ids:
        matches = _collect_image_lookup_matches(
            session=session,
            config=config,
            workspace_ids=fallback_workspace_ids,
            matcher=ref_match,
        )

    resolved = _choose_image_match(
        ctx,
        image_ref,
        matches,
        json_output=json_output,
        lookup_kind="image reference",
    )
    return resolved, None


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@click.command("list")
@click.option(
    "--workspace",
    help="Workspace name (from [workspaces])",
)
@click.option(
    "--workspace-id",
    help="Workspace ID (defaults to configured workspace)",
)
@click.option(
    "--source",
    "-s",
    type=click.Choice(_SOURCE_CHOICES, case_sensitive=False),
    default="official",
    show_default=True,
    help="Image source filter",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def list_images_cmd(
    ctx: Context,
    workspace: Optional[str],
    workspace_id: Optional[str],
    source: str,
    json_output: bool,
) -> None:
    """List available Docker images.

    \b
    Examples:
        inspire image list                              # Official images
        inspire image list --workspace gpu              # Resolve workspace alias from config
        inspire image list --workspace-id ws-...        # Query a specific workspace directly
        inspire image list --source private             # Your custom images
        inspire image list --source personal-visible    # Web UI "personal visible" tab
        inspire image list --source all                 # All sources
        inspire image list --source all --json          # JSON output
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Listing images requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )
    config = load_config(ctx)

    resolved_workspace_id: Optional[str] = None
    if workspace_id:
        resolved_workspace_id = workspace_id
    elif workspace:
        try:
            resolved_workspace_id = select_workspace_id(config, explicit_workspace_name=workspace)
        except ConfigError as e:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return
    else:
        try:
            resolved_workspace_id = select_workspace_id(config)
        except ConfigError as e:
            _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
            return

        resolved_workspace_id = resolved_workspace_id or getattr(session, "workspace_id", None)
        resolved_workspace_id = (
            None if resolved_workspace_id == DEFAULT_WORKSPACE_ID else resolved_workspace_id
        )
        if not resolved_workspace_id:
            _handle_error(
                ctx,
                "ConfigError",
                "No workspace_id configured or provided.",
                EXIT_CONFIG_ERROR,
                hint=(
                    "Use --workspace-id, set [workspaces].cpu/[workspaces].gpu in config.toml, "
                    "or set INSPIRE_WORKSPACE_ID."
                ),
            )
            return

    results: list[dict] = []

    try:
        if source == "all":
            for src_key in ("official", "public", "private"):
                items = browser_api_module.list_images_by_source(
                    source=src_key,
                    workspace_id=resolved_workspace_id,
                    session=session,
                )
                results.extend(_image_to_dict(img) for img in items)
        else:
            items = browser_api_module.list_images_by_source(
                source=source,
                workspace_id=resolved_workspace_id,
                session=session,
            )
            results.extend(_image_to_dict(img) for img in items)
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to list images: {e}", EXIT_API_ERROR)
        return

    if json_output:
        click.echo(json_formatter.format_json({"images": results, "total": len(results)}))
        return

    click.echo(human_formatter.format_image_list(results))


# ---------------------------------------------------------------------------
# detail
# ---------------------------------------------------------------------------


@click.command("detail")
@click.argument("image_id")
@click.option(
    "--workspace",
    help="Workspace name (from [workspaces])",
)
@click.option(
    "--workspace-id",
    help="Workspace ID (limits automatic image lookup to one workspace)",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def image_detail(
    ctx: Context,
    image_id: str,
    workspace: Optional[str],
    workspace_id: Optional[str],
    json_output: bool,
) -> None:
    """Show detailed information about an image.

    \b
    Examples:
        inspire image detail <image-id>
        inspire image detail kchen-slurm-ffmpeg:3.2
        inspire image detail docker.sii.shaipower.online/inspire-studio/kchen-slurm-ffmpeg:3.2
        inspire image detail kchen-slurm-ffmpeg:3.2 --workspace internet
        inspire image detail <image-id> --json
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Image detail requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )
    config = load_config(ctx)

    try:
        image_id, prefetched_image = _resolve_image_ref_for_detail(
            ctx,
            image_id,
            json_output=json_output,
            session=session,
            config=config,
            workspace=workspace,
            workspace_id=workspace_id,
        )
    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
        return

    try:
        image = prefetched_image or browser_api_module.get_image_detail(
            image_id=image_id, session=session
        )
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to get image detail: {e}", EXIT_API_ERROR)
        return

    if json_output:
        click.echo(json_formatter.format_json(_image_to_dict(image)))
        return

    click.echo(human_formatter.format_image_detail(_image_to_dict(image)))


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


@click.command("register")
@click.option(
    "--name",
    "-n",
    required=True,
    help="Image name (lowercase, digits, dashes, dots, underscores)",
)
@click.option(
    "--version",
    "-v",
    required=True,
    help="Image version tag (e.g., v1.0)",
)
@click.option(
    "--description",
    "-d",
    default="",
    help="Image description",
)
@click.option(
    "--visibility",
    type=click.Choice(["private", "public"], case_sensitive=False),
    default="private",
    show_default=True,
    help="Image visibility",
)
@click.option(
    "--method",
    type=click.Choice(["push", "address"], case_sensitive=False),
    default="push",
    show_default=True,
    help="'push': create a slot then docker-push your image; "
    "'address': register an image already hosted elsewhere",
)
@click.option(
    "--wait/--no-wait",
    default=False,
    help="Wait for image to reach READY status",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def register_image_cmd(
    ctx: Context,
    name: str,
    version: str,
    description: str,
    visibility: str,
    method: str,
    wait: bool,
    json_output: bool,
) -> None:
    """Register an external Docker image on the platform.

    This is for images you built outside the platform. To save a running
    notebook as an image, use 'inspire image save' instead.

    \b
    Push workflow (default):
      1. inspire image register -n my-img -v v1.0
      2. docker tag <local-image> <registry-url>   (shown in output)
      3. docker push <registry-url>
      4. Platform detects the push and marks the image READY.

    \b
    Address workflow:
      Register an image already hosted on a public/private registry.
      inspire image register -n my-img -v v1.0 --method address

    \b
    Examples:
        inspire image register -n my-pytorch -v v1.0
        inspire image register -n my-img -v v2.0 --method address
        inspire image register -n my-img -v v1.0 --visibility public --wait
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Registering images requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    visibility_value = (
        "VISIBILITY_PUBLIC" if visibility.lower() == "public" else "VISIBILITY_PRIVATE"
    )
    add_method_value = 2 if method.lower() == "address" else 0

    try:
        result = browser_api_module.create_image(
            name=name,
            version=version,
            description=description,
            visibility=visibility_value,
            add_method=add_method_value,
            session=session,
        )
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to register image: {e}", EXIT_API_ERROR)
        return

    image_data = result.get("image", {})
    image_id = image_data.get("image_id", "") or result.get("image_id", "")
    registry_url = image_data.get("address", "") or result.get("address", "")

    if wait and image_id:
        if not json_output:
            click.echo(f"Image '{image_id}' registered. Waiting for READY status...")
        try:
            browser_api_module.wait_for_image_ready(image_id=image_id, session=session)
            if not json_output:
                click.echo(f"Image '{image_id}' is now READY.")
        except (TimeoutError, ValueError) as e:
            _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
            return

    if json_output:
        click.echo(json_formatter.format_json({"image_id": image_id, "result": result}))
        return

    click.echo(f"Image registered: {image_id or 'unknown'}")
    if registry_url and method.lower() == "push":
        click.echo("\nTo push your image:")
        click.echo(f"  docker tag <local-image> {registry_url}")
        click.echo(f"  docker push {registry_url}")
    if not wait and image_id:
        click.echo(f"\nUse 'inspire image detail {image_id}' to check status.")


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


@click.command("save")
@click.argument("notebook_id")
@click.option(
    "--name",
    "-n",
    required=True,
    help="Name for the saved image",
)
@click.option(
    "--version",
    "-v",
    default="v1",
    show_default=True,
    help="Image version tag",
)
@click.option(
    "--description",
    "-d",
    default="",
    help="Image description",
)
@click.option(
    "--wait/--no-wait",
    default=False,
    help="Wait for image to reach READY status",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def save_image_cmd(
    ctx: Context,
    notebook_id: str,
    name: str,
    version: str,
    description: str,
    wait: bool,
    json_output: bool,
) -> None:
    """Save a running notebook as a custom Docker image.

    \b
    Examples:
        inspire image save <notebook-id> -n my-saved-image
        inspire image save <notebook-id> -n my-img -v v2 --wait
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Saving images requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    try:
        result = browser_api_module.save_notebook_as_image(
            notebook_id=notebook_id,
            name=name,
            version=version,
            description=description,
            session=session,
        )
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to save notebook as image: {e}", EXIT_API_ERROR)
        return

    image_id = result.get("image", {}).get("image_id", "") or result.get("image_id", "")

    if wait and image_id:
        if not json_output:
            click.echo(f"Image '{image_id}' is being saved. Waiting for READY status...")
        try:
            browser_api_module.wait_for_image_ready(image_id=image_id, session=session)
            if not json_output:
                click.echo(f"Image '{image_id}' is now READY.")
        except (TimeoutError, ValueError) as e:
            _handle_error(ctx, "APIError", str(e), EXIT_API_ERROR)
            return

    if json_output:
        click.echo(json_formatter.format_json({"image_id": image_id, "result": result}))
        return

    click.echo(f"Notebook saved as image: {image_id or 'unknown'}")
    if not wait and image_id:
        click.echo(f"Use 'inspire image detail {image_id}' to check build status.")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@click.command("delete")
@click.argument("image_id")
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def delete_image_cmd(
    ctx: Context,
    image_id: str,
    force: bool,
    json_output: bool,
) -> None:
    """Delete a custom Docker image.

    \b
    Examples:
        inspire image delete <image-id>
        inspire image delete <image-id> --force
    """
    json_output = resolve_json_output(ctx, json_output)

    session = require_web_session(
        ctx,
        hint=(
            "Deleting images requires web authentication. "
            "Set [auth].username/password in config.toml or "
            "INSPIRE_USERNAME/INSPIRE_PASSWORD."
        ),
    )

    image_id = _resolve_image_id(ctx, image_id, json_output, session)

    if not force and not json_output:
        if not click.confirm(f"Delete image '{image_id}'?"):
            click.echo("Cancelled.")
            return

    try:
        result = browser_api_module.delete_image(image_id=image_id, session=session)
    except Exception as e:
        _handle_error(ctx, "APIError", f"Failed to delete image: {e}", EXIT_API_ERROR)
        return

    if json_output:
        click.echo(
            json_formatter.format_json(
                {"image_id": image_id, "status": "deleted", "result": result}
            )
        )
        return

    click.echo(f"Image '{image_id}' has been deleted.")


# ---------------------------------------------------------------------------
# set-default
# ---------------------------------------------------------------------------


@click.command("set-default")
@click.option(
    "--job",
    "job_image",
    default=None,
    help="Set default image for jobs (written to [job].image in .inspire/config.toml)",
)
@click.option(
    "--notebook",
    "notebook_image",
    default=None,
    help="Set default image for notebooks (written to [notebook].image in .inspire/config.toml)",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Alias for global --json",
)
@pass_context
def set_default_image_cmd(
    ctx: Context,
    job_image: Optional[str],
    notebook_image: Optional[str],
    json_output: bool,
) -> None:
    """Save image preferences to .inspire/config.toml.

    \b
    Examples:
        inspire image set-default --job my-pytorch-image
        inspire image set-default --notebook my-notebook-image
        inspire image set-default --job img1 --notebook img2
    """
    json_output = resolve_json_output(ctx, json_output)

    if not job_image and not notebook_image:
        _handle_error(
            ctx,
            "ValidationError",
            "Specify at least one of --job or --notebook.",
            EXIT_VALIDATION_ERROR,
        )
        return

    config_path = Path(".inspire") / "config.toml"

    # Read existing config if present
    existing_data: dict = {}
    if config_path.exists():
        try:
            from inspire.config.toml import _load_toml

            existing_data = _load_toml(config_path)
        except Exception:
            existing_data = {}

    # Update the relevant sections
    updated: dict[str, str] = {}
    if job_image:
        if "job" not in existing_data:
            existing_data["job"] = {}
        existing_data["job"]["image"] = job_image
        updated["job.image"] = job_image

    if notebook_image:
        if "notebook" not in existing_data:
            existing_data["notebook"] = {}
        existing_data["notebook"]["image"] = notebook_image
        updated["notebook.image"] = notebook_image

    # Write back
    try:
        from inspire.cli.commands.init.toml_helpers import _toml_dumps

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_toml_dumps(existing_data), encoding="utf-8")
    except Exception as e:
        _handle_error(ctx, "ConfigError", f"Failed to write config: {e}", EXIT_CONFIG_ERROR)
        return

    if json_output:
        click.echo(
            json_formatter.format_json({"updated": updated, "config_path": str(config_path)})
        )
        return

    for key, value in updated.items():
        click.echo(f"Set {key} = {value!r} in {config_path}")


__all__ = [
    "delete_image_cmd",
    "image_detail",
    "list_images_cmd",
    "register_image_cmd",
    "save_image_cmd",
    "set_default_image_cmd",
]
