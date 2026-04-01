"""Helpers for preparing and caching HPC job submissions."""

from __future__ import annotations

import os
from typing import Any, Optional

from inspire.cli.utils.hpc_cache import HPCJobCache
from inspire.cli.utils.hpc_script import build_hpc_sbatch_script
from inspire.config import Config


def resolve_hpc_preset(config: Config, preset_name: str | None) -> dict[str, Any]:
    """Resolve an HPC preset by explicit or configured default name."""
    resolved_name = preset_name or config.hpc_default_preset
    if not resolved_name:
        raise ValueError("No HPC preset selected. Use --preset or configure hpc.default_preset.")

    preset = dict((config.hpc_presets or {}).get(resolved_name, {}))
    if not preset:
        raise ValueError(f"Unknown HPC preset: {resolved_name}")
    preset.setdefault("name", resolved_name)
    return preset


def merge_hpc_overrides(preset: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge CLI overrides on top of a preset."""
    merged = dict(preset)
    for key, value in overrides.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def build_hpc_create_payload(
    *,
    config: Config,
    name: str,
    project_id: str,
    workspace_id: str,
    image: Optional[str],
    image_type: Optional[str],
    merged_config: dict[str, Any],
    command: Optional[str] = None,
    script_body: Optional[str] = None,
) -> dict[str, Any]:
    """Build the final payload for the HPC create API."""
    final_image = image or merged_config.get("image") or config.hpc_image
    if not final_image:
        raise ValueError("No HPC image configured. Set --image or configure hpc.image.")

    final_image_type = image_type or merged_config.get("image_type") or config.hpc_image_type
    if script_body:
        entrypoint = script_body
    else:
        if not command:
            raise ValueError("Either command or script_body is required")
        entrypoint = build_hpc_sbatch_script(
            command=command,
            number_of_tasks=int(merged_config["number_of_tasks"]),
            cpus_per_task=int(merged_config["cpus_per_task"]),
            memory_per_cpu=str(merged_config["memory_per_cpu"]),
            time_limit=str(merged_config["time"]),
            extra_sbatch_lines=list(merged_config.get("extra_sbatch_lines", []) or []),
        )

    return {
        "name": name,
        "logic_compute_group_id": merged_config["logic_compute_group_id"],
        "project_id": project_id,
        "image": final_image,
        "image_type": final_image_type,
        "entrypoint": entrypoint,
        "instance_count": int(merged_config.get("instance_count", 1)),
        "workspace_id": workspace_id,
        "spec_id": merged_config["spec_id"],
        "ttl_after_finish_seconds": int(
            merged_config.get(
                "ttl_after_finish_seconds",
                config.hpc_ttl_after_finish_seconds,
            )
        ),
        "number_of_tasks": int(merged_config["number_of_tasks"]),
        "cpus_per_task": int(merged_config["cpus_per_task"]),
        "memory_per_cpu": str(merged_config["memory_per_cpu"]),
        "enable_hyper_threading": bool(merged_config.get("enable_hyper_threading", False)),
    }


def cache_created_hpc_job(
    *,
    config: Config,
    job_id: str,
    name: str,
    entrypoint: str,
    status: str = "PENDING",
    project: Optional[str] = None,
    cache_path: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Cache a newly created HPC job in its dedicated cache file."""
    resolved_cache_path = cache_path or os.getenv("INSPIRE_HPC_JOB_CACHE")
    cache = HPCJobCache(resolved_cache_path)
    if resolved_cache_path is None and config.job_cache_path:
        default_dir = cache.cache_path.parent
        cache = HPCJobCache(str(default_dir / "hpc_jobs.json"))
    cache.add_job(
        job_id=job_id,
        name=name,
        entrypoint=entrypoint,
        status=status,
        project=project,
        metadata=metadata,
    )


__all__ = [
    "build_hpc_create_payload",
    "cache_created_hpc_job",
    "merge_hpc_overrides",
    "resolve_hpc_preset",
]
