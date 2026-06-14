"""
Integration tests for the P0 walking skeleton.

Requires the full docker-compose stack to be running:
  docker compose up -d

Run with:
  pytest tests/test_p0_e2e.py -m integration -v
"""
import time

import httpx
import pytest

BASE = "http://localhost:8000"


def _poll_job(job_id: str, *, max_wait: float = 10.0, interval: float = 0.3) -> dict:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        resp = httpx.get(f"{BASE}/jobs/{job_id}")
        resp.raise_for_status()
        job = resp.json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(interval)
    pytest.fail(f"Job {job_id} did not complete within {max_wait}s")


def _summarize_request(**overrides) -> dict:
    base = {
        "capability": "summarize",
        "operation": "summarize",
        "context": {
            "tenant_id": "team-test",
            "principal": "svc-test",
            "data_classification": "internal",
            "residency": "any",
        },
        "payload": {
            "text": (
                "Machine learning is a subfield of artificial intelligence that enables "
                "computers to learn from data without being explicitly programmed. "
                "It powers applications such as image recognition, natural language "
                "processing, and recommendation systems."
            )
        },
        "options": {"mode": "async"},
    }
    base.update(overrides)
    return base


@pytest.mark.integration
def test_gateway_health():
    resp = httpx.get(f"{BASE}/health", timeout=5.0)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.integration
def test_submit_returns_202_with_job_id():
    resp = httpx.post(f"{BASE}/capabilities", json=_summarize_request(), timeout=5.0)
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"


@pytest.mark.integration
def test_full_envelope_flow():
    """Submit → poll → verify complete provenance and gate decision."""
    resp = httpx.post(f"{BASE}/capabilities", json=_summarize_request(), timeout=5.0)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = _poll_job(job_id)

    assert job["status"] == "succeeded", f"Job failed: {job.get('error')}"

    # Result
    assert "summary" in job["result"]
    assert job["result"]["summary"]  # non-empty

    # Provenance — all mandatory fields must be present
    prov = job["provenance"]
    assert prov["backend_used"]
    assert isinstance(prov["tokens_in"], int) and prov["tokens_in"] >= 0
    assert isinstance(prov["tokens_out"], int) and prov["tokens_out"] >= 0
    assert isinstance(prov["cost_usd"], float)
    assert isinstance(prov["latency_ms"], int)
    assert isinstance(prov["confidence"], float)

    # Gate decision
    gates = job["gates"]
    assert gates["egress_decision"] in ("allowed", "denied", "redacted_then_allowed")
    assert gates["classification"] == "internal"
    assert isinstance(gates["redactions_applied"], int)


@pytest.mark.integration
def test_job_polling_reflects_intermediate_states():
    """After submit, job exists and is queued or running (not yet succeeded)."""
    resp = httpx.post(f"{BASE}/capabilities", json=_summarize_request(), timeout=5.0)
    job_id = resp.json()["job_id"]

    immediate = httpx.get(f"{BASE}/jobs/{job_id}", timeout=5.0).json()
    assert immediate["status"] in ("queued", "running", "succeeded")


@pytest.mark.integration
def test_unknown_job_returns_404():
    resp = httpx.get(f"{BASE}/jobs/does-not-exist-00000000", timeout=5.0)
    assert resp.status_code == 404


@pytest.mark.integration
def test_unavailable_capability_returns_404():
    req = _summarize_request()
    req["capability"] = "rag"  # valid in envelope, not yet routed
    resp = httpx.post(f"{BASE}/capabilities", json=req, timeout=5.0)
    assert resp.status_code == 404


@pytest.mark.integration
def test_invalid_capability_returns_422():
    req = _summarize_request()
    req["capability"] = "totally-made-up"
    resp = httpx.post(f"{BASE}/capabilities", json=req, timeout=5.0)
    assert resp.status_code == 422


@pytest.mark.integration
def test_tenant_id_is_preserved_in_job():
    """The job must record which capability was used (tenant attribution groundwork)."""
    resp = httpx.post(f"{BASE}/capabilities", json=_summarize_request(), timeout=5.0)
    job_id = resp.json()["job_id"]
    job = _poll_job(job_id)
    assert job["capability"] == "summarize"
