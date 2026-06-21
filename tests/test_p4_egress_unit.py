"""
P4 unit tests for the egress gate — no docker, no OPA server required.

Tests cover:
  - Local capabilities bypass the gate entirely (no OPA call)
  - Confidential / restricted → denied for remote capabilities
  - public / internal + any residency → allowed for remote capabilities
  - on_prem_only residency → denied for remote capabilities
  - OPA unavailable → fail-closed deny
  - Presidio redacts email and phone from payload text
  - Redacted payload does not contain raw PII
  - Audit-log hash is not the raw payload text
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gateway import egress_gate
from gateway.audit_log import hash_payload


# ── helpers ────────────────────────────────────────────────────────────────────

def _gate(capability, classification, residency, payload, *, opa_allow=True, opa_reason=""):
    """Call evaluate() with a mocked OPA response."""
    with patch("gateway.egress_gate._query_opa", return_value=(opa_allow, opa_reason)):
        return egress_gate.evaluate(capability, classification, residency, payload)


# ── Local-capability bypass ────────────────────────────────────────────────────

def test_anomaly_bypasses_gate_entirely():
    with patch("gateway.egress_gate._query_opa") as mock_opa:
        decision, _, _, _ = egress_gate.evaluate("anomaly", "confidential", "on_prem_only", {})
    assert decision == "local"
    mock_opa.assert_not_called()


def test_ocr_bypasses_gate_entirely():
    with patch("gateway.egress_gate._query_opa") as mock_opa:
        decision, _, _, _ = egress_gate.evaluate("ocr", "restricted", "on_prem_only", {})
    assert decision == "local"
    mock_opa.assert_not_called()


# ── Remote capability — denial cases ──────────────────────────────────────────

def test_confidential_to_remote_denied():
    decision, _, _, reason = _gate("summarize", "confidential", "any", {"text": "secret"}, opa_allow=False, opa_reason="classification:confidential — data may not egress to a remote provider")
    assert decision == "denied"
    assert "confidential" in reason


def test_restricted_to_remote_denied():
    decision, _, _, reason = _gate("vision", "restricted", "any", {}, opa_allow=False, opa_reason="classification:restricted — data may not egress to a remote provider")
    assert decision == "denied"
    assert "restricted" in reason


def test_on_prem_only_residency_denied():
    decision, _, _, reason = _gate("rag", "internal", "on_prem_only", {}, opa_allow=False, opa_reason="residency:on_prem_only — remote egress is prohibited for this request")
    assert decision == "denied"
    assert "on_prem_only" in reason


# ── Remote capability — allowed cases ─────────────────────────────────────────

def test_public_to_remote_allowed():
    decision, _, n, _ = _gate("summarize", "public", "any", {"text": "hello world"})
    assert decision == "allowed"
    assert n == 0


def test_internal_to_remote_allowed():
    decision, _, n, _ = _gate("rag", "internal", "any", {"query": "Explain retrieval augmented generation."})
    assert decision == "allowed"
    assert n == 0


# ── OPA unavailable → fail-closed ─────────────────────────────────────────────

def test_opa_unavailable_fail_closed():
    with patch("gateway.egress_gate.httpx.Client") as mock_cls:
        mock_ctx = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.side_effect = Exception("connection refused")

        allow, reason = egress_gate._query_opa("summarize", "internal", "any")

    assert not allow
    assert "opa_unavailable" in reason


# ── Presidio PII redaction ─────────────────────────────────────────────────────

def test_presidio_redacts_email():
    decision, redacted, n, _ = _gate(
        "summarize", "internal", "any",
        {"text": "Contact me at john.doe@example.com for details"},
    )
    assert decision == "redacted_then_allowed"
    assert n >= 1
    assert "john.doe@example.com" not in redacted["text"]


def test_presidio_redacts_phone():
    decision, redacted, n, _ = _gate(
        "summarize", "internal", "any",
        {"text": "Call me at 212-555-0100"},
    )
    assert decision == "redacted_then_allowed"
    assert n >= 1
    assert "212-555-0100" not in redacted["text"]


def test_presidio_redacts_nested_text():
    payload = {"outer": {"inner": "Reach me at nested@corp.io or 212-555-7890"}}
    decision, redacted, n, _ = _gate("rag", "internal", "any", payload)
    assert decision == "redacted_then_allowed"
    assert n >= 1
    assert "nested@corp.io" not in redacted["outer"]["inner"]


def test_clean_payload_returns_allowed_not_redacted():
    payload = {"text": "The quick brown fox jumps over the lazy dog."}
    decision, redacted, n, _ = _gate("summarize", "internal", "any", payload)
    assert decision == "allowed"
    assert n == 0
    assert redacted == payload


# ── Audit log payload hashing ─────────────────────────────────────────────────

def test_hash_payload_returns_64_char_hex():
    h = hash_payload({"text": "sensitive SSN 123-45-6789"})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_payload_does_not_contain_raw_text():
    h = hash_payload({"text": "user@example.com secret data"})
    assert "user@example.com" not in h
    assert "secret" not in h


def test_hash_is_deterministic():
    payload = {"a": 1, "b": "two"}
    assert hash_payload(payload) == hash_payload(payload)


def test_hash_differs_for_different_payloads():
    assert hash_payload({"x": 1}) != hash_payload({"x": 2})
