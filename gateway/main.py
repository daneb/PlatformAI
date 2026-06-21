from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

from .envelope import CapabilityRequest, GateDecision, Provenance
from . import jobs
from . import egress_gate
from .audit_log import emit as audit_emit, hash_payload

app = FastAPI(title="AI Platform Gateway")

# Spoke registry — extended each phase. Only capabilities listed here are routable.
_ROUTES: dict[str, str] = {
    "summarize": "http://capability-summarize:8001",
    "rag": "http://capability-rag:8003",
    "ocr": "http://capability-ocr:8004",
    "idp": "http://capability-idp:8005",
    "vision": "http://capability-vision:8006",
    "anomaly": "http://capability-anomaly:8007",
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
    t0 = time.perf_counter()
    jobs.update(job_id, status="running")

    payload_sha = hash_payload(req.payload)

    # Run egress gate in thread pool (Presidio + OPA are synchronous / CPU-bound).
    decision, redacted_payload, n_redactions, deny_reason = await asyncio.to_thread(
        egress_gate.evaluate,
        req.capability,
        req.context.data_classification,
        req.context.residency,
        req.payload,
    )

    if decision == "denied":
        jobs.update(job_id, status="denied", error=deny_reason)
        audit_emit(
            trace_id=req.context.trace_id,
            tenant_id=req.context.tenant_id,
            principal=req.context.principal,
            capability=req.capability,
            operation=req.operation,
            classification=req.context.data_classification,
            residency=req.context.residency,
            payload_sha256=payload_sha,
            egress_decision="denied",
            redactions_applied=0,
            deny_reason=deny_reason,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
        return

    dispatch_req = req.model_copy(update={"payload": redacted_payload})

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{route}/execute", json=dispatch_req.model_dump())
            resp.raise_for_status()
            data = resp.json()

        provenance = Provenance(**data["provenance"])
        # Gateway is authoritative for egress_decision; "local" is surfaced as "allowed".
        egress_decision = decision if decision != "local" else "allowed"
        gates = GateDecision(
            classification=req.context.data_classification,
            redactions_applied=n_redactions,
            egress_decision=egress_decision,
        )
        jobs.update(
            job_id,
            status="succeeded",
            result=data["result"],
            provenance=provenance,
            gates=gates,
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        audit_emit(
            trace_id=req.context.trace_id,
            tenant_id=req.context.tenant_id,
            principal=req.context.principal,
            capability=req.capability,
            operation=req.operation,
            classification=req.context.data_classification,
            residency=req.context.residency,
            payload_sha256=payload_sha,
            egress_decision=egress_decision,
            redactions_applied=n_redactions,
            deny_reason="",
            backend_used=provenance.backend_used,
            cost_usd=provenance.cost_usd,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        jobs.update(job_id, status="failed", error=str(exc))
        audit_emit(
            trace_id=req.context.trace_id,
            tenant_id=req.context.tenant_id,
            principal=req.context.principal,
            capability=req.capability,
            operation=req.operation,
            classification=req.context.data_classification,
            residency=req.context.residency,
            payload_sha256=payload_sha,
            egress_decision="error",
            redactions_applied=n_redactions,
            deny_reason=str(exc),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
