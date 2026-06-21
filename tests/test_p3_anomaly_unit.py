"""Unit tests for P3 anomaly capability — rule engine and IsolationForest model. No docker required."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from capabilities.anomaly.main import app, _models

client = TestClient(app)

_NORMAL = [{"x": float(i), "y": float(i * 0.5)} for i in range(60)]
_ANOMALY = {"x": 5000.0, "y": 5000.0}
_MODEL_ID = "unit-test"


def _execute(operation: str, payload: dict) -> dict:
    resp = client.post(
        "/execute",
        json={
            "capability": "anomaly",
            "operation": operation,
            "context": {"tenant_id": "t", "principal": "p", "data_classification": "internal"},
            "payload": payload,
            "options": {},
        },
    )
    return resp


# ── fit ──────────────────────────────────────────────────────────────────────

def test_fit_trains_model():
    resp = _execute("fit", {"dataset": _NORMAL, "model_id": _MODEL_ID})
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["model_id"] == _MODEL_ID
    assert result["samples_trained"] == 60
    assert result["features"] == 2


def test_fit_missing_dataset_returns_422():
    resp = _execute("fit", {"model_id": "x"})
    assert resp.status_code == 422


def test_fit_stores_model():
    assert _MODEL_ID in _models


# ── score ─────────────────────────────────────────────────────────────────────

def test_score_normal_records_not_flagged():
    resp = _execute("score", {"records": _NORMAL[25:30], "model_id": _MODEL_ID})
    assert resp.status_code == 200
    scored = resp.json()["result"]["scored_records"]
    assert all(r["is_anomaly"] is False for r in scored)


def test_score_anomalous_record_flagged():
    resp = _execute("score", {"records": [_ANOMALY], "model_id": _MODEL_ID})
    assert resp.status_code == 200
    scored = resp.json()["result"]["scored_records"]
    assert scored[0]["is_anomaly"] is True


def test_score_unknown_model_returns_404():
    resp = _execute("score", {"records": _NORMAL[:2], "model_id": "nonexistent"})
    assert resp.status_code == 404


def test_score_missing_records_returns_422():
    resp = _execute("score", {"model_id": _MODEL_ID})
    assert resp.status_code == 422


def test_score_result_preserves_original_fields():
    resp = _execute("score", {"records": [{"x": 1.0, "y": 0.5}], "model_id": _MODEL_ID})
    scored = resp.json()["result"]["scored_records"][0]
    assert scored["x"] == 1.0
    assert scored["y"] == 0.5
    assert "anomaly_score" in scored
    assert "is_anomaly" in scored


# ── stream_score ──────────────────────────────────────────────────────────────

def test_stream_score_returns_single_record_with_score():
    resp = _execute("stream_score", {"record": _NORMAL[0], "model_id": _MODEL_ID})
    assert resp.status_code == 200
    record = resp.json()["result"]["record"]
    assert "anomaly_score" in record
    assert "is_anomaly" in record
    assert isinstance(record["is_anomaly"], bool)


def test_stream_score_anomaly_detected():
    resp = _execute("stream_score", {"record": _ANOMALY, "model_id": _MODEL_ID})
    assert resp.status_code == 200
    assert resp.json()["result"]["record"]["is_anomaly"] is True


def test_stream_score_unknown_model_returns_404():
    resp = _execute("stream_score", {"record": _NORMAL[0], "model_id": "no-such-model"})
    assert resp.status_code == 404


def test_stream_score_missing_record_returns_422():
    resp = _execute("stream_score", {"model_id": _MODEL_ID})
    assert resp.status_code == 422


# ── provenance + gates ────────────────────────────────────────────────────────

def test_provenance_has_required_fields():
    resp = _execute("score", {"records": _NORMAL[:3], "model_id": _MODEL_ID})
    prov = resp.json()["provenance"]
    assert prov["backend_used"] == "sklearn-isolation-forest"
    assert prov["cost_usd"] == 0.0
    assert isinstance(prov["latency_ms"], int)
    assert prov["tokens_in"] == 0


def test_gates_show_local_egress():
    resp = _execute("score", {"records": _NORMAL[:3], "model_id": _MODEL_ID})
    gates = resp.json()["gates"]
    assert gates["egress_decision"] == "allowed"
    assert gates["classification"] == "internal"
    assert gates["redactions_applied"] == 0


def test_unknown_operation_returns_422():
    resp = _execute("detect", {})
    assert resp.status_code == 422
