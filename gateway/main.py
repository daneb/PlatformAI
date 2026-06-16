from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

from .envelope import CapabilityRequest, GateDecision, Provenance
from . import jobs

app = FastAPI(title="AI Platform Gateway")

# Spoke registry — extended each phase. Only capabilities listed here are routable.
_ROUTES: dict[str, str] = {
    "summarize": "http://capability-summarize:8001",
    "rag": "http://capability-rag:8003",
}


@app.post("/capabilities", status_code=202)
async def submit(req: CapabilityRequest, background: BackgroundTasks) -> dict[str, str]:
    route = _ROUTES.get(req.capability)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Capability '{req.capability}' not yet available")

    job_id = str(uuid.uuid4())
    jobs.create(req.capability, job_id)
    background.add_task(_dispatch, job_id, req, route)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
async def poll(job_id: str) -> Any:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _dispatch(job_id: str, req: CapabilityRequest, route: str) -> None:
    jobs.update(job_id, status="running")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{route}/execute", json=req.model_dump())
            resp.raise_for_status()
            data = resp.json()

        provenance = Provenance(**data["provenance"])
        gates = GateDecision(**data["gates"])
        jobs.update(
            job_id,
            status="succeeded",
            result=data["result"],
            provenance=provenance,
            gates=gates,
        )
    except Exception as exc:
        jobs.update(job_id, status="failed", error=str(exc))
