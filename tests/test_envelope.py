"""Unit tests for the request/response envelope — no docker required."""
import pytest
from pydantic import ValidationError

from gateway.envelope import CapabilityRequest, JobResponse, Provenance, GateDecision


def _valid_request(**overrides) -> dict:
    base = {
        "capability": "summarize",
        "operation": "summarize",
        "context": {
            "tenant_id": "team-test",
            "principal": "svc-test",
        },
        "payload": {"text": "hello world"},
    }
    base.update(overrides)
    return base


def test_valid_request_parses():
    req = CapabilityRequest(**_valid_request())
    assert req.capability == "summarize"
    assert req.context.data_classification == "internal"  # default
    assert req.options.mode == "async"                     # default
    assert req.context.trace_id  # auto-generated uuid


def test_unknown_capability_rejected():
    with pytest.raises(ValidationError):
        CapabilityRequest(**_valid_request(capability="unknown"))


def test_unknown_classification_rejected():
    with pytest.raises(ValidationError):
        CapabilityRequest(**_valid_request(context={
            "tenant_id": "t1",
            "principal": "u1",
            "data_classification": "top-secret",  # not in the Literal
        }))


def test_trace_id_is_unique():
    r1 = CapabilityRequest(**_valid_request())
    r2 = CapabilityRequest(**_valid_request())
    assert r1.context.trace_id != r2.context.trace_id


def test_provenance_fields():
    p = Provenance(
        backend_used="stub-model",
        tokens_in=50,
        tokens_out=20,
        cost_usd=0.0,
        latency_ms=123,
        confidence=1.0,
    )
    assert p.backend_used == "stub-model"


def test_gate_decision_valid_values():
    g = GateDecision(
        classification="internal",
        redactions_applied=0,
        egress_decision="allowed",
    )
    assert g.egress_decision == "allowed"


def test_gate_decision_rejects_unknown_egress():
    with pytest.raises(ValidationError):
        GateDecision(
            classification="internal",
            redactions_applied=0,
            egress_decision="maybe",
        )
