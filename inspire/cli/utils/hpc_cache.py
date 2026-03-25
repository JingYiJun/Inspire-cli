"""Local cache for tracking submitted HPC jobs."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HPCJobCache:
    """Local cache for tracking HPC jobs."""

    def __init__(self, cache_path: Optional[str] = None):
        if cache_path:
            self.cache_path = Path(os.path.expanduser(cache_path))
        else:
            self.cache_path = Path.home() / ".inspire" / "hpc_jobs.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.cache_path.exists():
            return {}
        try:
            with self.cache_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, jobs: Dict[str, Dict[str, Any]]) -> None:
        try:
            with self.cache_path.open("w", encoding="utf-8") as handle:
                json.dump(jobs, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.warning("Failed to write HPC job cache at %s: %s", self.cache_path, exc)

    def add_job(
        self,
        *,
        job_id: str,
        name: str,
        entrypoint: str,
        status: str = "PENDING",
        project: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        jobs = self._load()
        jobs[job_id] = {
            "name": name,
            "entrypoint": entrypoint,
            "status": status,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        if project is not None:
            jobs[job_id]["project"] = project
        if metadata:
            jobs[job_id].update(metadata)
        self._save(jobs)

    def upsert_job(self, *, job_id: str, data: dict[str, Any]) -> None:
        jobs = self._load()
        current = dict(jobs.get(job_id, {}))
        current.update(data)
        current.setdefault("created_at", datetime.now().isoformat())
        current["updated_at"] = datetime.now().isoformat()
        jobs[job_id] = current
        self._save(jobs)

    def update_status(self, job_id: str, status: str) -> None:
        jobs = self._load()
        if job_id in jobs:
            jobs[job_id]["status"] = status
            jobs[job_id]["updated_at"] = datetime.now().isoformat()
            self._save(jobs)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        jobs = self._load()
        if job_id in jobs:
            return {"job_id": job_id, **jobs[job_id]}
        return None

    def list_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        jobs = [{"job_id": key, **value} for key, value in self._load().items()]
        jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        if limit > 0:
            return jobs[:limit]
        return jobs


__all__ = ["HPCJobCache"]
