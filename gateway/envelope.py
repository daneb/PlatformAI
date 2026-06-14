from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class RequestContext(BaseModel):
    tenant_id: str
    principal: str
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    data_classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    residency: Literal["any", "on_prem_only"] = "any"


class RequestOptions(BaseModel):
    mode: Literal["async", "sync"] = "async"
    backend_hint: Optional[str] = None
    max_cost_usd: float = 0.05


class CapabilityRequest(BaseModel):
    capability: Literal["summarize", "rag", "ocr", "idp", "vision", "anomaly"]
    operation: str
    context: RequestContext
    payload: dict[str, Any]
    options: RequestOptions = Field(default_factory=RequestOptions)


class Provenance(BaseModel):
    backend_used: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    confidence: float


class GateDecision(BaseModel):
    classification: str
    redactions_applied: int = 0
    egress_decision: Literal["allowed", "denied", "redacted_then_allowed"]


class JobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed", "denied"]
    capability: str
    created_at: datetime
    updated_at: datetime
    result: Optional[dict[str, Any]] = None
    provenance: Optional[Provenance] = None
    gates: Optional[GateDecision] = None
    error: Optional[str] = None
