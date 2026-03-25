from pathlib import Path

from inspire.cli.utils.hpc_submit import (
    build_hpc_create_payload,
    cache_created_hpc_job,
    merge_hpc_overrides,
    resolve_hpc_preset,
)
from inspire.config import Config


def _cfg(tmp_path: Path) -> Config:
    return Config(
        username="demo",
        password="secret",
        job_cache_path=str(tmp_path / "jobs.json"),
        hpc_presets={
            "cpu-small": {
                "workspace": "hpc",
                "logic_compute_group_id": "lcg-hpc",
                "spec_id": "quota-cpu-small",
                "number_of_tasks": 1,
                "cpus_per_task": 4,
                "memory_per_cpu": "4G",
                "time": "0-12:00:00",
                "enable_hyper_threading": False,
            }
        },
    )


def test_resolve_hpc_preset_returns_named_preset(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    preset = resolve_hpc_preset(cfg, "cpu-small")

    assert preset["spec_id"] == "quota-cpu-small"


def test_merge_hpc_overrides_prefers_cli_values(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    preset = resolve_hpc_preset(cfg, "cpu-small")

    merged = merge_hpc_overrides(
        preset,
        {"cpus_per_task": 8, "number_of_tasks": 2, "memory_per_cpu": "8G"},
    )

    assert merged["cpus_per_task"] == 8
    assert merged["number_of_tasks"] == 2
    assert merged["memory_per_cpu"] == "8G"


def test_build_hpc_create_payload_includes_generated_entrypoint(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    payload = build_hpc_create_payload(
        config=cfg,
        name="demo",
        project_id="project-demo",
        workspace_id="ws-hpc",
        image="docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest",
        image_type="SOURCE_PUBLIC",
        merged_config={
            "logic_compute_group_id": "lcg-hpc",
            "spec_id": "quota-cpu-small",
            "number_of_tasks": 2,
            "cpus_per_task": 8,
            "memory_per_cpu": "4G",
            "time": "0-12:00:00",
            "enable_hyper_threading": False,
        },
        command="python main.py",
    )

    assert payload["logic_compute_group_id"] == "lcg-hpc"
    assert payload["spec_id"] == "quota-cpu-small"
    assert payload["entrypoint"].startswith("#!/bin/bash\n")
    assert "#SBATCH --mem=64G" in payload["entrypoint"]


def test_cache_created_hpc_job_writes_independent_cache(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cache_path = tmp_path / "hpc_jobs.json"

    cache_created_hpc_job(
        config=cfg,
        cache_path=str(cache_path),
        job_id="hpc-job-7768776e-16b5-4b09-a61e-e5341c7dxxxx",
        name="demo",
        entrypoint="#!/bin/bash\nsrun bash -lc 'python main.py'\n",
        status="PENDING",
        project="CPU Demo",
    )

    content = cache_path.read_text(encoding="utf-8")
    assert "hpc-job-7768776e-16b5-4b09-a61e-e5341c7dxxxx" in content
    assert "python main.py" in content
