"""
P2 OCR + IDP integration tests — requires the full docker-compose stack.

  docker compose up -d
  pytest tests/test_p2_idp_e2e.py -m integration -v
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


def _render_document(lines: list[str]) -> bytes:
    """Render plain text lines onto a white PNG — clean, large-font input for a deterministic Tesseract read."""
    font = ImageFont.load_default(size=32)
    img = Image.new("RGB", (900, 60 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((20, 20 + 60 * i), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_document(lines: list[str]) -> str:
    key = f"test-{uuid.uuid4().hex[:8]}.png"
    _s3_client().put_object(Bucket=MINIO_BUCKET, Key=key, Body=_render_document(lines))
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


# ── OCR ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_ocr_extract_text_reads_rendered_document():
    object_ref = _upload_document(["INVOICE", "Vendor: Acme Corp", "Amount: 1234.56"])
    job = _poll(_submit("ocr", "extract_text", {"object_ref": object_ref}))
    assert job["status"] == "succeeded", f"OCR failed: {job.get('error')}"
    text = job["result"]["text"]
    assert "INVOICE" in text
    assert "Acme" in text


@pytest.mark.integration
def test_ocr_extract_layout_returns_words_with_boxes():
    object_ref = _upload_document(["RECEIPT", "Total: 42.00"])
    job = _poll(_submit("ocr", "extract_layout", {"object_ref": object_ref}))
    assert job["status"] == "succeeded", f"OCR failed: {job.get('error')}"
    words = job["result"]["words"]
    assert len(words) > 0
    assert all({"text", "confidence", "left", "top", "width", "height"} <= w.keys() for w in words)


@pytest.mark.integration
def test_ocr_unknown_object_ref_fails_job():
    job = _poll(_submit("ocr", "extract_text", {"object_ref": "s3://documents/does-not-exist.png"}))
    assert job["status"] == "failed"


# ── IDP ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_idp_full_pipeline_invoice_passes_validation():
    object_ref = _upload_document(
        ["INVOICE", "Vendor: Acme Corp", "Amount: 1234.56", "Date: 2026-06-01"]
    )
    schema = ["Vendor", "Amount", "Date"]
    ruleset = [
        {"field": "Vendor", "rule": "required"},
        {"field": "Amount", "rule": "numeric"},
    ]
    job = _poll(
        _submit("idp", "process", {"object_ref": object_ref, "schema": schema, "ruleset": ruleset}),
        max_wait=30.0,
    )
    assert job["status"] == "succeeded", f"IDP failed: {job.get('error')}"

    result = job["result"]
    assert result["doc_type"] == "invoice"
    assert result["fields"]["Vendor"] == "Acme Corp"
    assert result["validation"]["valid"] is True
    assert result["validation"]["errors"] == []

    prov = job["provenance"]
    assert prov["backend_used"]
    assert isinstance(prov["tokens_in"], int) and prov["tokens_in"] >= 0
    assert isinstance(prov["latency_ms"], int)

    gates = job["gates"]
    assert gates["egress_decision"] == "allowed"
    assert gates["classification"] == "internal"


@pytest.mark.integration
def test_idp_missing_required_field_fails_validation():
    object_ref = _upload_document(["RECEIPT", "Thank you for your purchase"])
    schema = ["Vendor", "Amount"]
    ruleset = [{"field": "Vendor", "rule": "required"}]
    job = _poll(
        _submit("idp", "process", {"object_ref": object_ref, "schema": schema, "ruleset": ruleset})
    )
    assert job["status"] == "succeeded"
    assert job["result"]["validation"]["valid"] is False
    assert any("Vendor" in e for e in job["result"]["validation"]["errors"])


@pytest.mark.integration
def test_idp_missing_object_ref_returns_failed_job():
    job = _poll(_submit("idp", "process", {"schema": ["Vendor"], "ruleset": []}))
    assert job["status"] == "failed"


@pytest.mark.integration
def test_idp_missing_schema_returns_failed_job():
    object_ref = _upload_document(["CONTRACT"])
    job = _poll(_submit("idp", "process", {"object_ref": object_ref, "ruleset": []}))
    assert job["status"] == "failed"
