"""
P3 Vision + Anomaly integration tests — requires the full docker-compose stack.

  docker compose up -d
  pytest tests/test_p3_e2e.py -m integration -v
"""
from __future__ import annotations

import io
import time
import uuid

import boto3
import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

BASE = "http://localhost:8000"
MINIO_ENDPOINT = "http://localhost:9000"
MINIO_BUCKET = "documents"


def _poll(job_id: str, max_wait: float = 30.0) -> dict:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        resp = httpx.get(f"{BASE}/jobs/{job_id}", timeout=5.0)
        resp.raise_for_status()
        job = resp.json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.3)
    pytest.fail(f"Job {job_id} did not complete within {max_wait}s")


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id="platform",
        aws_secret_access_key="platform123",
    )


def _render_image(lines: list[str]) -> bytes:
    font = ImageFont.load_default(size=32)
    img = Image.new("RGB", (900, 60 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((20, 20 + 60 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image(lines: list[str]) -> str:
    key = f"test-{uuid.uuid4().hex[:8]}.png"
    _s3_client().put_object(Bucket=MINIO_BUCKET, Key=key, Body=_render_image(lines))
    return f"s3://{MINIO_BUCKET}/{key}"


def _submit(capability: str, operation: str, payload: dict) -> str:
    body = {
        "capability": capability,
        "operation": operation,
        "context": {"tenant_id": "team-test", "principal": "svc-test", "data_classification": "internal"},
        "payload": payload,
        "options": {"mode": "async"},
    }
    resp = httpx.post(f"{BASE}/capabilities", json=body, timeout=10.0)
    resp.raise_for_status()
    return resp.json()["job_id"]


# ── Vision ───────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_vision_describe_returns_description():
    object_ref = _upload_image(["SAMPLE IMAGE", "Platform AI P3"])
    job = _poll(_submit("vision", "describe", {"object_ref": object_ref}))
    assert job["status"] == "succeeded", f"Vision describe failed: {job.get('error')}"
    assert "description" in job["result"]
    assert job["result"]["description"]
    prov = job["provenance"]
    assert prov["backend_used"] == "deepseek-v4-flash"
    assert isinstance(prov["latency_ms"], int)


@pytest.mark.integration
def test_vision_detect_returns_detections_for_requested_labels():
    object_ref = _upload_image(["DETECTION TEST"])
    job = _poll(_submit("vision", "detect", {"object_ref": object_ref, "labels": ["text", "white background"]}))
    assert job["status"] == "succeeded", f"Vision detect failed: {job.get('error')}"
    detections = job["result"]["detections"]
    assert len(detections) == 2
    labels_returned = {d["label"] for d in detections}
    assert "text" in labels_returned
    assert "white background" in labels_returned
    assert all("present" in d and "confidence" in d for d in detections)


@pytest.mark.integration
def test_vision_ask_returns_answer():
    object_ref = _upload_image(["QUESTION IMAGE", "42"])
    job = _poll(_submit("vision", "ask", {"object_ref": object_ref, "question": "What number is in this image?"}))
    assert job["status"] == "succeeded", f"Vision ask failed: {job.get('error')}"
    assert "answer" in job["result"]
    assert job["result"]["answer"]


@pytest.mark.integration
def test_vision_missing_object_ref_fails_job():
    job = _poll(_submit("vision", "describe", {}))
    assert job["status"] == "failed"


@pytest.mark.integration
def test_vision_detect_missing_labels_fails_job():
    object_ref = _upload_image(["TEST"])
    job = _poll(_submit("vision", "detect", {"object_ref": object_ref}))
    assert job["status"] == "failed"


# ── Anomaly ──────────────────────────────────────────────────────────────────

_NORMAL_DATASET = [{"cpu": float(i % 10 + 10), "mem": float(i % 5 + 20)} for i in range(80)]
_ANOMALY_RECORD = {"cpu": 999.0, "mem": 999.0}
_MODEL_ID = f"e2e-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
def test_anomaly_fit_trains_model():
    job = _poll(_submit("anomaly", "fit", {"dataset": _NORMAL_DATASET, "model_id": _MODEL_ID}))
    assert job["status"] == "succeeded", f"Anomaly fit failed: {job.get('error')}"
    result = job["result"]
    assert result["model_id"] == _MODEL_ID
    assert result["samples_trained"] == 80
    prov = job["provenance"]
    assert prov["backend_used"] == "sklearn-isolation-forest"
    assert prov["cost_usd"] == 0.0
    gates = job["gates"]
    assert gates["egress_decision"] == "allowed"


@pytest.mark.integration
def test_anomaly_score_batch_flags_outlier():
    records = _NORMAL_DATASET[:3] + [_ANOMALY_RECORD]
    job = _poll(_submit("anomaly", "score", {"records": records, "model_id": _MODEL_ID}))
    assert job["status"] == "succeeded", f"Anomaly score failed: {job.get('error')}"
    scored = job["result"]["scored_records"]
    assert len(scored) == 4
    # The obvious outlier (cpu=999, mem=999) must be flagged; training records near edges may also score high
    outlier = scored[-1]
    assert outlier["is_anomaly"] is True
    assert outlier["anomaly_score"] > 0.5
    assert all("anomaly_score" in r and "is_anomaly" in r for r in scored)


@pytest.mark.integration
def test_anomaly_stream_score_single_record():
    job = _poll(_submit("anomaly", "stream_score", {"record": _NORMAL_DATASET[0], "model_id": _MODEL_ID}))
    assert job["status"] == "succeeded", f"Anomaly stream_score failed: {job.get('error')}"
    record = job["result"]["record"]
    assert "anomaly_score" in record
    assert "is_anomaly" in record
    assert isinstance(record["is_anomaly"], bool)
    assert 0.0 <= record["anomaly_score"] <= 1.0


@pytest.mark.integration
def test_anomaly_score_without_fit_fails_job():
    job = _poll(_submit("anomaly", "score", {"records": [{"x": 1.0}], "model_id": "never-fitted"}))
    assert job["status"] == "failed"
