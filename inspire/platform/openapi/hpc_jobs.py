"""HPC job-related helpers for the Inspire OpenAPI client."""

from __future__ import annotations

import logging
from typing import Any, Dict

from inspire.platform.openapi.errors import (
    InspireAPIError,
    JobCreationError,
    JobNotFoundError,
    _translate_api_error,
    _validate_hpc_job_id_format,
)

logger = logging.getLogger(__name__)


def create_hpc_job(  # noqa: PLR0913
    api,  # noqa: ANN001
    *,
    name: str,
    logic_compute_group_id: str,
    project_id: str,
    image: str,
    image_type: str,
    entrypoint: str,
    instance_count: int,
    task_priority: int,
    workspace_id: str,
    spec_id: str,
    ttl_after_finish_seconds: int,
    number_of_tasks: int,
    cpus_per_task: int,
    memory_per_cpu: str,
    enable_hyper_threading: bool,
) -> Dict[str, Any]:
    """Create an HPC job."""
    api._check_authentication()
    api._validate_required_params(
        name=name,
        logic_compute_group_id=logic_compute_group_id,
        project_id=project_id,
        image=image,
        image_type=image_type,
        entrypoint=entrypoint,
        workspace_id=workspace_id,
        spec_id=spec_id,
        memory_per_cpu=memory_per_cpu,
    )

    payload = {
        "name": name,
        "logic_compute_group_id": logic_compute_group_id,
        "project_id": project_id,
        "image": image,
        "image_type": image_type,
        "entrypoint": entrypoint,
        "instance_count": instance_count,
        "task_priority": task_priority,
        "workspace_id": workspace_id,
        "spec_id": spec_id,
        "ttl_after_finish_seconds": ttl_after_finish_seconds,
        "number_of_tasks": number_of_tasks,
        "cpus_per_task": cpus_per_task,
        "memory_per_cpu": memory_per_cpu,
        "enable_hyper_threading": enable_hyper_threading,
    }

    result = api._make_request("POST", api.endpoints.HPC_JOB_CREATE, payload)
    if result.get("code") == 0:
        logger.info("🚀 HPC job created successfully")
        return result

    error_code = result.get("code")
    error_msg = result.get("message", "Unknown error")
    friendly_msg = _translate_api_error(error_code, error_msg)
    raise JobCreationError(f"Failed to create HPC job: {friendly_msg}")


def get_hpc_job_detail(api, job_id: str) -> Dict[str, Any]:  # noqa: ANN001
    """Get HPC job details."""
    api._check_authentication()
    api._validate_required_params(job_id=job_id)

    format_error = _validate_hpc_job_id_format(job_id)
    if format_error:
        raise JobNotFoundError(f"Invalid HPC job ID '{job_id}': {format_error}")

    payload = {"job_id": job_id}
    result = api._make_request("POST", api.endpoints.HPC_JOB_DETAIL, payload)
    if result.get("code") == 0:
        return result

    error_code = result.get("code")
    error_msg = result.get("message", "Unknown error")
    friendly_msg = _translate_api_error(error_code, error_msg)
    if error_code == 100002:
        raise JobNotFoundError(f"Failed to get HPC job details for '{job_id}': {friendly_msg}")
    raise InspireAPIError(f"Failed to get HPC job details: {friendly_msg}")


def stop_hpc_job(api, job_id: str) -> bool:  # noqa: ANN001
    """Stop an HPC job."""
    api._check_authentication()
    api._validate_required_params(job_id=job_id)

    format_error = _validate_hpc_job_id_format(job_id)
    if format_error:
        raise JobNotFoundError(f"Invalid HPC job ID '{job_id}': {format_error}")

    payload = {"job_id": job_id}
    result = api._make_request("POST", api.endpoints.HPC_JOB_STOP, payload)
    if result.get("code") == 0:
        logger.info("🛑 HPC job %s stopped successfully.", job_id)
        return True

    error_code = result.get("code")
    error_msg = result.get("message", "Unknown error")
    friendly_msg = _translate_api_error(error_code, error_msg)
    if error_code == 100002:
        raise JobNotFoundError(f"Failed to stop HPC job '{job_id}': {friendly_msg}")
    raise InspireAPIError(f"Failed to stop HPC job: {friendly_msg}")


__all__ = ["create_hpc_job", "get_hpc_job_detail", "stop_hpc_job"]
