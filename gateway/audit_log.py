from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

_log = logging.getLogger("platform.audit")


def emit(
    *,
    trace_id: str,
    tenant_id: str,
    principal: str,
    capability: str,
    operation: str,
    classification: str,
    residency: str,
    payload_sha256: str,
    egress_decision: str,
    redactions_applied: int,
    deny_reason: str,
    backend_used: str = "",
    cost_usd: float = 0.0,
    latency_ms: int = 0,
) -> None:
    record: dict[str, Any] = {
        "audit": True,
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "principal": principal,
        "capability": capability,
        "operation": operation,
        "classification": classification,
        "residency": residency,
        "payload_sha256": payload_sha256,
        "egress_decision": egress_decision,
        "redactions_applied": redactions_applied,
        "deny_reason": deny_reason or None,
        "backend_used": backend_used or None,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
    }
    _log.info(json.dumps(record))


def hash_payload(payload: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical JSON serialisation of the payload."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
