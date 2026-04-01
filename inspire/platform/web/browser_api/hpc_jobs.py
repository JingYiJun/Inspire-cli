"""Browser (web-session) APIs for HPC job listing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from inspire.platform.web.browser_api.core import _browser_api_path, _get_base_url, _request_json
from inspire.platform.web.session import DEFAULT_WORKSPACE_ID, WebSession, get_web_session

__all__ = ["HPCJobInfo", "list_hpc_jobs"]


@dataclass
class HPCJobInfo:
    """HPC job information exposed by the web UI API."""

    job_id: str
    name: str
    status: str
    created_at: str
    created_by_name: str
    created_by_id: str
    project_id: str
    project_name: str
    compute_group_name: str
    workspace_id: str
    sbatch_script: str

    @classmethod
    def from_api_response(cls, data: dict) -> "HPCJobInfo":
        created_by = data.get("created_by", {}) or {}
        return cls(
            job_id=str(data.get("job_id", "")),
            name=str(data.get("job_name") or data.get("name") or ""),
            status=str(data.get("status", "")),
            created_at=str(data.get("created_at", "")),
            created_by_name=str(created_by.get("name", "")),
            created_by_id=str(created_by.get("id", "")),
            project_id=str(data.get("project_id", "")),
            project_name=str(data.get("project_name", "")),
            compute_group_name=str(data.get("logic_compute_group_name", "")),
            workspace_id=str(data.get("workspace_id", "")),
            sbatch_script=str(data.get("sbatch_script", "")),
        )


def list_hpc_jobs(
    workspace_id: Optional[str] = None,
    created_by: Optional[str] = None,
    status: Optional[str] = None,
    page_num: int = 1,
    page_size: int = 50,
    session: Optional[WebSession] = None,
) -> tuple[list[HPCJobInfo], int]:
    """List HPC jobs using the browser API."""
    if session is None:
        session = get_web_session()

    if workspace_id is None:
        workspace_id = session.workspace_id or DEFAULT_WORKSPACE_ID

    body: dict[str, Any] = {
        "workspace_id": workspace_id,
        "page_num": page_num,
        "page_size": page_size,
    }

    if created_by:
        body["created_by"] = created_by
    if status:
        body["status"] = status

    data = _request_json(
        session,
        "POST",
        _browser_api_path("/hpc_jobs/list"),
        referer=f"{_get_base_url()}/jobs/hpc",
        body=body,
        timeout=30,
    )

    if data.get("code") != 0:
        raise ValueError(f"API error: {data.get('message')}")

    jobs_data = data.get("data", {}).get("jobs", [])
    total = data.get("data", {}).get("total", 0)
    jobs = [HPCJobInfo.from_api_response(job) for job in jobs_data]
    return jobs, total
