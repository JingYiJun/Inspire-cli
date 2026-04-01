from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from inspire import config as config_module
from inspire.cli.commands.hpc import hpc_commands as hpc_commands_module
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


def test_hpc_create_script_file_strips_sbatch_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, api = _patch_hpc_runtime(monkeypatch, tmp_path)
    script_path = tmp_path / "job.sh"
    script_path.write_text(
        "#!/bin/bash\n"
        "#SBATCH -o /hpc_logs/slurm-%j.out\n"
        "#SBATCH --time=0-01:00:00\n"
        "\n"
        "srun /bin/sh -lc 'sleep 300'\n",
        encoding="utf-8",
    )

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
            "--script-file",
            str(script_path),
        ],
    )

    assert result.exit_code == 0
    assert api.calls["create_hpc_job"]["entrypoint"] == "srun /bin/sh -lc 'sleep 300'"


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

    list_result = runner.invoke(cli_main, ["hpc", "list", "--cache"])
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


def test_hpc_wait_treats_stopped_as_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, api = _patch_hpc_runtime(monkeypatch, tmp_path)
    api.detail_responses = [
        {
            "data": {
                "job_id": TEST_HPC_JOB_ID,
                "name": "demo",
                "status": "STOPPED",
            }
        }
    ]

    runner = CliRunner()
    result = runner.invoke(
        cli_main, ["hpc", "wait", TEST_HPC_JOB_ID, "--timeout", "5", "--interval", "0"]
    )

    assert result.exit_code == 0
    assert "STOPPED" in result.output


def test_hpc_commands_read_config_files_not_env_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, api = _patch_hpc_runtime(monkeypatch, tmp_path)

    def fail_from_env(cls, require_target_dir: bool = False):  # type: ignore[override]
        raise AssertionError("Config.from_env should not be used for hpc commands")

    monkeypatch.setattr(config_module.Config, "from_env", classmethod(fail_from_env))
    runner = CliRunner()

    create_result = runner.invoke(
        cli_main,
        ["hpc", "create", "--name", "demo", "--command", "python main.py"],
    )
    assert create_result.exit_code == 0

    status_result = runner.invoke(cli_main, ["hpc", "status", TEST_HPC_JOB_ID])
    assert status_result.exit_code == 0

    stop_result = runner.invoke(cli_main, ["hpc", "stop", TEST_HPC_JOB_ID])
    assert stop_result.exit_code == 0

    list_result = runner.invoke(cli_main, ["hpc", "list", "--cache"])
    assert list_result.exit_code == 0
    assert api is not None
    assert config is not None


def test_hpc_list_uses_remote_current_user_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, _ = _patch_hpc_runtime(monkeypatch, tmp_path)
    session = object()
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(hpc_commands_module, "require_web_session", lambda ctx, hint: session)
    monkeypatch.setattr(
        browser_api_module, "get_current_user", lambda session=None: {"id": "user-me"}
    )

    def fake_list_hpc_jobs(**kwargs: Any) -> tuple[list[SimpleNamespace], int]:
        calls.append(kwargs)
        return (
            [
                SimpleNamespace(
                    job_id="hpc-job-11111111-1111-1111-1111-111111111111",
                    name="kchen-demo-1",
                    status="RUNNING",
                    created_at="2026-03-26 10:00:00",
                    created_by_id="user-me",
                    created_by_name="me",
                    project_id="project-demo",
                    project_name="Demo Project",
                    compute_group_name="HPC",
                    workspace_id=config.workspace_hpc_id,
                )
            ],
            1,
        )

    monkeypatch.setattr(browser_api_module, "list_hpc_jobs", fake_list_hpc_jobs)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["hpc", "list", "--json"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["workspace_id"] == config.workspace_hpc_id
    assert calls[0]["created_by"] == "user-me"
    assert '"job_id": "hpc-job-11111111-1111-1111-1111-111111111111"' in result.output
    assert (
        '"url": "https://example.invalid/jobs/hpcDetail/hpc-job-11111111-1111-1111-1111-111111111111"'
        in result.output
    )


def test_hpc_list_all_queries_cpu_and_hpc_workspaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, _ = _patch_hpc_runtime(monkeypatch, tmp_path)
    session = object()
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(hpc_commands_module, "require_web_session", lambda ctx, hint: session)
    monkeypatch.setattr(
        browser_api_module, "get_current_user", lambda session=None: {"id": "user-me"}
    )

    def fake_list_hpc_jobs(**kwargs: Any) -> tuple[list[SimpleNamespace], int]:
        calls.append(kwargs)
        ws_id = kwargs["workspace_id"]
        suffix = "cpu" if ws_id == config.workspace_cpu_id else "hpc"
        return (
            [
                SimpleNamespace(
                    job_id=f"hpc-job-{suffix}000-1111-1111-1111-111111111111",
                    name=f"kchen-{suffix}",
                    status="SUCCEEDED",
                    created_at="2026-03-26 09:00:00",
                    created_by_id="user-me",
                    created_by_name="me",
                    project_id="project-demo",
                    project_name="Demo Project",
                    compute_group_name=suffix.upper(),
                    workspace_id=ws_id,
                )
            ],
            1,
        )

    monkeypatch.setattr(browser_api_module, "list_hpc_jobs", fake_list_hpc_jobs)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["hpc", "list", "--all", "--json"])

    assert result.exit_code == 0
    assert [call["workspace_id"] for call in calls] == [
        config.workspace_cpu_id,
        config.workspace_hpc_id,
    ]
    assert '"workspace": "cpu"' in result.output
    assert '"workspace": "hpc"' in result.output


def test_hpc_list_cache_mode_keeps_legacy_behavior(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, _ = _patch_hpc_runtime(monkeypatch, tmp_path)
    hpc_cache_path = tmp_path / "hpc_jobs.json"
    monkeypatch.setenv("INSPIRE_HPC_JOB_CACHE", str(hpc_cache_path))

    runner = CliRunner()
    create_result = runner.invoke(
        cli_main,
        ["hpc", "create", "--name", "demo", "--command", "python main.py"],
    )
    assert create_result.exit_code == 0

    monkeypatch.setattr(
        hpc_commands_module,
        "require_web_session",
        lambda ctx, hint: (_ for _ in ()).throw(
            AssertionError("remote session should not be requested in --cache mode")
        ),
    )
    monkeypatch.setattr(
        browser_api_module,
        "list_hpc_jobs",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("remote list should not be used in --cache mode")
        ),
    )

    result = runner.invoke(cli_main, ["hpc", "list", "--cache"])

    assert result.exit_code == 0
    assert TEST_HPC_JOB_ID in result.output


def test_hpc_list_help_mentions_remote_and_cache_modes() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["hpc", "list", "--help"])

    assert result.exit_code == 0
    assert "current account" in result.output.lower()
    assert "--status" in result.output
    assert "--all" in result.output
    assert "--cache" in result.output
    assert "--workspace" in result.output


def test_hpc_list_passes_single_status_filter_to_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, _ = _patch_hpc_runtime(monkeypatch, tmp_path)
    session = object()
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(hpc_commands_module, "require_web_session", lambda ctx, hint: session)
    monkeypatch.setattr(
        browser_api_module, "get_current_user", lambda session=None: {"id": "user-me"}
    )

    def fake_list_hpc_jobs(**kwargs: Any) -> tuple[list[SimpleNamespace], int]:
        calls.append(kwargs)
        return (
            [
                SimpleNamespace(
                    job_id="hpc-job-22222222-1111-1111-1111-111111111111",
                    name="kchen-running",
                    status="RUNNING",
                    created_at="2026-03-26 11:00:00",
                    created_by_id="user-me",
                    created_by_name="me",
                    project_id="project-demo",
                    project_name="Demo Project",
                    compute_group_name="HPC",
                    workspace_id=config.workspace_hpc_id,
                )
            ],
            1,
        )

    monkeypatch.setattr(browser_api_module, "list_hpc_jobs", fake_list_hpc_jobs)

    runner = CliRunner()
    result = runner.invoke(cli_main, ["hpc", "list", "-s", "running", "--json"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["status"] == "RUNNING"
    assert '"status": "RUNNING"' in result.output


def test_hpc_list_repeats_remote_calls_for_multiple_status_filters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, _ = _patch_hpc_runtime(monkeypatch, tmp_path)
    session = object()
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(hpc_commands_module, "require_web_session", lambda ctx, hint: session)
    monkeypatch.setattr(
        browser_api_module, "get_current_user", lambda session=None: {"id": "user-me"}
    )

    def fake_list_hpc_jobs(**kwargs: Any) -> tuple[list[SimpleNamespace], int]:
        calls.append(kwargs)
        status = kwargs["status"]
        return (
            [
                SimpleNamespace(
                    job_id=f"hpc-job-{status.lower()}-1111-1111-1111-111111111111",
                    name=f"kchen-{status.lower()}",
                    status=status,
                    created_at=f"2026-03-26 1{len(calls)}:00:00",
                    created_by_id="user-me",
                    created_by_name="me",
                    project_id="project-demo",
                    project_name="Demo Project",
                    compute_group_name="HPC",
                    workspace_id=config.workspace_hpc_id,
                )
            ],
            1,
        )

    monkeypatch.setattr(browser_api_module, "list_hpc_jobs", fake_list_hpc_jobs)

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["hpc", "list", "-s", "running", "-s", "stopped", "--json"],
    )

    assert result.exit_code == 0
    assert [call["status"] for call in calls] == ["RUNNING", "STOPPED"]
    assert '"status": "RUNNING"' in result.output
    assert '"status": "STOPPED"' in result.output


def test_hpc_list_cache_mode_filters_statuses_locally(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_hpc_runtime(monkeypatch, tmp_path)
    hpc_cache_path = tmp_path / "hpc_jobs.json"
    monkeypatch.setenv("INSPIRE_HPC_JOB_CACHE", str(hpc_cache_path))

    cache = hpc_commands_module._cache(hpc_commands_module._load_config(require_credentials=False))
    cache.add_job(
        job_id="hpc-job-aaaaaaaa-1111-1111-1111-111111111111",
        name="running-job",
        entrypoint="srun python main.py",
        status="RUNNING",
    )
    cache.add_job(
        job_id="hpc-job-bbbbbbbb-1111-1111-1111-111111111111",
        name="stopped-job",
        entrypoint="srun python other.py",
        status="STOPPED",
    )

    runner = CliRunner()
    filtered = runner.invoke(cli_main, ["hpc", "list", "--cache", "-s", "running"])

    assert filtered.exit_code == 0
    assert "running-job" in filtered.output
    assert "stopped-job" not in filtered.output
