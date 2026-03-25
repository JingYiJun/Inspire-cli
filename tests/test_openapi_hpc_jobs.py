from types import SimpleNamespace

from inspire.platform.openapi.hpc_jobs import create_hpc_job, get_hpc_job_detail, stop_hpc_job


class _DummyAPI:
    def __init__(self) -> None:
        self.endpoints = SimpleNamespace(
            HPC_JOB_CREATE="/openapi/v1/hpc_jobs/create",
            HPC_JOB_DETAIL="/openapi/v1/hpc_jobs/detail",
            HPC_JOB_STOP="/openapi/v1/hpc_jobs/stop",
        )
        self.last_request: tuple[str, str, dict] | None = None

    def _check_authentication(self) -> None:  # noqa: D401
        return None

    def _validate_required_params(self, **kwargs) -> None:  # noqa: ANN003
        assert all(kwargs.values())

    def _make_request(self, method: str, endpoint: str, payload: dict) -> dict:
        self.last_request = (method, endpoint, payload)
        return {"code": 0, "data": {"job_id": "hpc-job-7768776e-16b5-4b09-a61e-e5341c7dxxxx"}}


def test_create_hpc_job_builds_payload() -> None:
    api = _DummyAPI()

    create_hpc_job(
        api,
        name="demo",
        logic_compute_group_id="lcg-hpc",
        project_id="project-demo",
        image="docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest",
        image_type="SOURCE_PUBLIC",
        entrypoint="#!/bin/bash\nsrun python main.py\n",
        instance_count=1,
        task_priority=4,
        workspace_id="ws-hpc",
        spec_id="quota-cpu-small",
        ttl_after_finish_seconds=900,
        number_of_tasks=2,
        cpus_per_task=8,
        memory_per_cpu="4G",
        enable_hyper_threading=False,
    )

    assert api.last_request is not None
    method, endpoint, payload = api.last_request
    assert method == "POST"
    assert endpoint == "/openapi/v1/hpc_jobs/create"
    assert payload == {
        "name": "demo",
        "logic_compute_group_id": "lcg-hpc",
        "project_id": "project-demo",
        "image": "docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest",
        "image_type": "SOURCE_PUBLIC",
        "entrypoint": "#!/bin/bash\nsrun python main.py\n",
        "instance_count": 1,
        "task_priority": 4,
        "workspace_id": "ws-hpc",
        "spec_id": "quota-cpu-small",
        "ttl_after_finish_seconds": 900,
        "number_of_tasks": 2,
        "cpus_per_task": 8,
        "memory_per_cpu": "4G",
        "enable_hyper_threading": False,
    }


def test_get_hpc_job_detail_uses_hpc_endpoint() -> None:
    api = _DummyAPI()

    get_hpc_job_detail(api, "hpc-job-7768776e-16b5-4b09-a61e-e5341c7dxxxx")

    assert api.last_request == (
        "POST",
        "/openapi/v1/hpc_jobs/detail",
        {"job_id": "hpc-job-7768776e-16b5-4b09-a61e-e5341c7dxxxx"},
    )


def test_stop_hpc_job_uses_hpc_endpoint() -> None:
    api = _DummyAPI()

    stop_hpc_job(api, "hpc-job-ccbf7e37-f28e-4eef-8fbe-ee6f04d3xxxx")

    assert api.last_request == (
        "POST",
        "/openapi/v1/hpc_jobs/stop",
        {"job_id": "hpc-job-ccbf7e37-f28e-4eef-8fbe-ee6f04d3xxxx"},
    )
