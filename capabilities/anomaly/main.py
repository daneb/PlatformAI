from __future__ import annotations

import time
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sklearn.ensemble import IsolationForest

app = FastAPI(title="Anomaly Capability")

# model_id → {clf, features, train_score_min, train_score_max}
_models: dict[str, dict[str, Any]] = {}


class ExecuteRequest(BaseModel):
    capability: str
    operation: str
    context: dict[str, Any]
    payload: dict[str, Any]
    options: dict[str, Any]


@app.post("/execute")
async def execute(req: ExecuteRequest) -> dict[str, Any]:
    if req.operation == "fit":
        return _fit(req)
    if req.operation == "score":
        return _score(req)
    if req.operation == "stream_score":
        return _stream_score(req)
    raise HTTPException(status_code=422, detail=f"Unknown operation: {req.operation!r}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _gates(req: ExecuteRequest) -> dict[str, Any]:
    return {
        "classification": req.context.get("data_classification", "internal"),
        "redactions_applied": 0,
        "egress_decision": "allowed",
    }


def _to_matrix(records: list[dict[str, Any]], features: list[str]) -> np.ndarray:
    try:
        return np.array([[float(r.get(f, 0.0)) for f in features] for r in records], dtype=float)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"All record values must be numeric: {exc}") from exc


def _normalize(score: float, s_min: float, s_max: float) -> float:
    # score_samples returns negative values; lower = more anomalous.
    # Map to [0, 1] where 1 = most anomalous, using training distribution as reference.
    normalized = (score - s_min) / (s_max - s_min + 1e-9)
    return float(max(0.0, min(1.0, 1.0 - normalized)))


def _provenance(backend: str, latency_ms: int) -> dict[str, Any]:
    return {
        "backend_used": backend,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "latency_ms": latency_ms,
        "confidence": 1.0,
    }


# ── operations ───────────────────────────────────────────────────────────────

def _fit(req: ExecuteRequest) -> dict[str, Any]:
    dataset = req.payload.get("dataset")
    model_id = req.payload.get("model_id", "default")
    if not dataset:
        raise HTTPException(status_code=422, detail="payload.dataset is required")

    features = list(dataset[0].keys())
    t0 = time.monotonic()
    X = _to_matrix(dataset, features)
    clf = IsolationForest(n_estimators=100, contamination="auto", random_state=42)
    clf.fit(X)
    scores = clf.score_samples(X)
    _models[model_id] = {
        "clf": clf,
        "features": features,
        "train_score_min": float(scores.min()),
        "train_score_max": float(scores.max()),
    }
    latency_ms = int((time.monotonic() - t0) * 1000)

    return {
        "result": {"model_id": model_id, "samples_trained": len(dataset), "features": len(features)},
        "provenance": _provenance("sklearn-isolation-forest", latency_ms),
        "gates": _gates(req),
    }


def _score(req: ExecuteRequest) -> dict[str, Any]:
    records = req.payload.get("records")
    model_id = req.payload.get("model_id", "default")
    if not records:
        raise HTTPException(status_code=422, detail="payload.records is required")
    if model_id not in _models:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found — call fit first")

    m = _models[model_id]
    t0 = time.monotonic()
    X = _to_matrix(records, m["features"])
    raw_scores = m["clf"].score_samples(X)
    predictions = m["clf"].predict(X)

    scored = [
        {
            **records[i],
            "anomaly_score": _normalize(float(raw_scores[i]), m["train_score_min"], m["train_score_max"]),
            "is_anomaly": bool(predictions[i] == -1),
        }
        for i in range(len(records))
    ]
    return {
        "result": {"scored_records": scored},
        "provenance": _provenance("sklearn-isolation-forest", int((time.monotonic() - t0) * 1000)),
        "gates": _gates(req),
    }


def _stream_score(req: ExecuteRequest) -> dict[str, Any]:
    record = req.payload.get("record")
    model_id = req.payload.get("model_id", "default")
    if record is None:
        raise HTTPException(status_code=422, detail="payload.record is required")
    if model_id not in _models:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found — call fit first")

    m = _models[model_id]
    t0 = time.monotonic()
    X = _to_matrix([record], m["features"])
    raw_score = float(m["clf"].score_samples(X)[0])
    is_anomaly = bool(m["clf"].predict(X)[0] == -1)

    return {
        "result": {
            "record": {
                **record,
                "anomaly_score": _normalize(raw_score, m["train_score_min"], m["train_score_max"]),
                "is_anomaly": is_anomaly,
            }
        },
        "provenance": _provenance("sklearn-isolation-forest", int((time.monotonic() - t0) * 1000)),
        "gates": _gates(req),
    }
