from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .envelope import GateDecision, JobResponse, Provenance

_store: dict[str, JobResponse] = {}


def create(capability: str, job_id: str) -> JobResponse:
    now = datetime.now(timezone.utc)
    job = JobResponse(
        job_id=job_id,
        status="queued",
        capability=capability,
        created_at=now,
        updated_at=now,
    )
    _store[job_id] = job
    return job


def get(job_id: str) -> Optional[JobResponse]:
    return _store.get(job_id)


def update(job_id: str, **fields: Any) -> Optional[JobResponse]:
    job = _store.get(job_id)
    if job is None:
        return None
    updated = job.model_copy(update={"updated_at": datetime.now(timezone.utc), **fields})
    _store[job_id] = updated
    return updated
