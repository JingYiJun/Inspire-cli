"""Helpers for normalizing HPC preset configuration."""

from __future__ import annotations

from typing import Any


def _coerce_preset_value(key: str, value: Any) -> Any:
    if key in {
        "instance_count",
        "task_priority",
        "ttl_after_finish_seconds",
        "number_of_tasks",
        "cpus_per_task",
    }:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if key == "enable_hyper_threading":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return text in {"1", "true", "yes", "on"}
    if isinstance(value, str):
        return value.strip()
    return value


def normalize_hpc_presets(raw_value: Any) -> dict[str, dict[str, Any]]:
    """Normalize `[hpc.presets.*]` into a predictable mapping."""
    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_item in raw_value.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_item, dict):
            continue

        preset: dict[str, Any] = {}
        for raw_key, raw_value_item in raw_item.items():
            key = str(raw_key).strip()
            if not key:
                continue
            preset[key] = _coerce_preset_value(key, raw_value_item)

        if preset:
            normalized[name] = preset

    return normalized


def merge_hpc_presets(
    base: dict[str, dict[str, Any]],
    override: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge preset mappings with per-preset override semantics."""
    merged = {name: dict(values) for name, values in base.items()}
    for name, values in override.items():
        current = dict(merged.get(name, {}))
        current.update(values)
        merged[name] = current
    return merged


__all__ = ["merge_hpc_presets", "normalize_hpc_presets"]
