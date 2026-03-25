from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from inspire import config as config_module
from inspire.cli.main import main as cli_main
from inspire.cli.utils import auth as auth_module
from inspire.platform.web.browser_api import ProjectInfo
from inspire.platform.web import browser_api as browser_api_module

TEST_HPC_JOB_ID = "hpc-job-7768776e-16b5-4b09-a61e-e5341c7dxxxx"


class DummyHPCAPI:
    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
        self.detail_responses = [
            {
                "data": {
                    "job_id": TEST_HPC_JOB_ID,
                    "name": "demo",
                    "status": "RUNNING",
                    "logic_compute_group_id": "lcg-hpc",
                    "workspace_id": "ws-11111111-1111-1111-1111-111111111111",
                    "image": "docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest",
                    "number_of_tasks": 2,
                    "cpus_per_task": 8,
                    "memory_per_cpu": "4G",
                }
            },
            {
                "data": {
                    "job_id": TEST_HPC_JOB_ID,
                    "name": "demo",
                    "status": "SUCCEEDED",
                    "logic_compute_group_id": "lcg-hpc",
                    "workspace_id": "ws-11111111-1111-1111-1111-111111111111",
                    "image": "docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest",
                    "number_of_tasks": 2,
                    "cpus_per_task": 8,
                    "memory_per_cpu": "4G",
                }
            },
        ]

    def create_hpc_job(self, **kwargs: Any) -> dict[str, Any]:
        self.calls["create_hpc_job"] = kwargs
        return {"data": {"job_id": TEST_HPC_JOB_ID}}

    def get_hpc_job_detail(self, job_id: str) -> dict[str, Any]:
        self.calls.setdefault("get_hpc_job_detail", []).append(job_id)
        if len(self.calls["get_hpc_job_detail"]) == 1:
            return self.detail_responses[0]
        return self.detail_responses[-1]

    def stop_hpc_job(self, job_id: str) -> bool:
        self.calls.setdefault("stop_hpc_job", []).append(job_id)
        return True


def _make_config(tmp_path: Path) -> config_module.Config:
    return config_module.Config(
        username="user",
        password="pass",
        base_url="https://example.invalid",
        target_dir=str(tmp_path / "logs"),
        job_cache_path=str(tmp_path / "jobs.json"),
        log_cache_dir=str(tmp_path / "log_cache"),
        job_project_id="project-demo",
        workspace_cpu_id="ws-22222222-2222-2222-2222-222222222222",
        workspace_hpc_id="ws-11111111-1111-1111-1111-111111111111",
        hpc_image="docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest",
        hpc_image_type="SOURCE_PUBLIC",
        hpc_priority=4,
        hpc_ttl_after_finish_seconds=900,
        hpc_default_preset="cpu-small",
        hpc_presets={
            "cpu-small": {
                "workspace": "hpc",
                "logic_compute_group_id": "lcg-hpc",
                "spec_id": "quota-cpu-small",
                "number_of_tasks": 2,
                "cpus_per_task": 8,
                "memory_per_cpu": "4G",
                "time": "0-12:00:00",
                "enable_hyper_threading": False,
            }
        },
    )


def _patch_hpc_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[config_module.Config, DummyHPCAPI]:
    config = _make_config(tmp_path)
    api = DummyHPCAPI()

    def fake_from_env(cls, require_target_dir: bool = False) -> config_module.Config:  # type: ignore[override]
        return config

    def fake_from_files_and_env(
        cls,
        require_target_dir: bool = False,
        require_credentials: bool = True,
    ) -> tuple[config_module.Config, dict[str, str]]:  # type: ignore[override]
        return config, {}

    monkeypatch.setattr(config_module.Config, "from_env", classmethod(fake_from_env))
    monkeypatch.setattr(
        config_module.Config, "from_files_and_env", classmethod(fake_from_files_and_env)
    )
    monkeypatch.setattr(auth_module.AuthManager, "get_api", lambda _cls, cfg=None: api)
    auth_module.AuthManager.clear_cache()
    monkeypatch.setattr(
        browser_api_module,
        "list_projects",
        lambda workspace_id=None, session=None: [
            ProjectInfo(
                project_id="project-demo",
                name="Demo Project",
                workspace_id="ws-11111111-1111-1111-1111-111111111111",
                member_gpu_limit=True,
                member_remain_gpu_hours=100.0,
            )
        ],
    )
    monkeypatch.setattr(browser_api_module, "check_scheduling_health", lambda **_: {})
    return config, api


def test_hpc_create_uses_preset_and_creates_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, api = _patch_hpc_runtime(monkeypatch, tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["hpc", "create", "--name", "demo", "--preset", "cpu-small", "--command", "python main.py"],
    )

    assert result.exit_code == 0
    assert "inspire hpc status" in result.output
    assert api.calls["create_hpc_job"]["spec_id"] == "quota-cpu-small"
    assert "srun bash -lc 'python main.py'" in api.calls["create_hpc_job"]["entrypoint"]


def test_hpc_create_rejects_command_with_script_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_hpc_runtime(monkeypatch, tmp_path)
    script_path = tmp_path / "job.sh"
    script_path.write_text("#!/bin/bash\nsrun python main.py\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "hpc",
            "create",
            "--name",
            "demo",
            "--preset",
            "cpu-small",
            "--command",
            "python main.py",
            "--script-file",
            str(script_path),
        ],
    )

    assert result.exit_code != 0
    assert "Cannot use --command with --script-file" in result.output


def test_hpc_status_stop_list_wait_and_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, api = _patch_hpc_runtime(monkeypatch, tmp_path)
    hpc_cache_path = tmp_path / "hpc_jobs.json"
    monkeypatch.setenv("INSPIRE_HPC_JOB_CACHE", str(hpc_cache_path))

    runner = CliRunner()
    create_result = runner.invoke(
        cli_main,
        ["hpc", "create", "--name", "demo", "--command", "python main.py"],
    )
    assert create_result.exit_code == 0

    status_result = runner.invoke(cli_main, ["hpc", "status", TEST_HPC_JOB_ID])
    assert status_result.exit_code == 0
    assert "RUNNING" in status_result.output or "SUCCEEDED" in status_result.output

    script_result = runner.invoke(cli_main, ["hpc", "script", TEST_HPC_JOB_ID])
    assert script_result.exit_code == 0
    assert "srun bash -lc 'python main.py'" in script_result.output

    list_result = runner.invoke(cli_main, ["hpc", "list"])
    assert list_result.exit_code == 0
    assert TEST_HPC_JOB_ID in list_result.output

    wait_result = runner.invoke(
        cli_main, ["hpc", "wait", TEST_HPC_JOB_ID, "--timeout", "5", "--interval", "0"]
    )
    assert wait_result.exit_code == 0

    stop_result = runner.invoke(cli_main, ["hpc", "stop", TEST_HPC_JOB_ID])
    assert stop_result.exit_code == 0
    assert api.calls["stop_hpc_job"] == [TEST_HPC_JOB_ID]
    assert config is not None
