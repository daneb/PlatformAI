"""
P1 RAG integration tests — requires the full docker-compose stack.

  docker compose up -d
  pytest tests/test_p1_rag_e2e.py -m integration -v
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest

BASE = "http://localhost:8000"
_COLLECTION = f"test-{uuid.uuid4().hex[:8]}"


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


def _rag_request(operation: str, payload: dict, **ctx_overrides) -> dict:
    ctx = {
        "tenant_id": "team-test",
        "principal": "svc-test",
        "data_classification": "internal",
        **ctx_overrides,
    }
    return {
        "capability": "rag",
        "operation": operation,
        "context": ctx,
        "payload": {**payload, "collection": _COLLECTION},
        "options": {"mode": "async"},
    }


# ── basic routing ─────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_rag_capability_routes_via_gateway():
    resp = httpx.post(f"{BASE}/capabilities", json=_rag_request("ingest", {"text": "hello"}), timeout=5.0)
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body


@pytest.mark.integration
def test_unavailable_rag_op_returns_422():
    """Gateway accepts the request but spoke returns 422 for unknown ops."""
    resp = httpx.post(
        f"{BASE}/capabilities",
        json=_rag_request("reindex", {"collection": _COLLECTION}),
        timeout=5.0,
    )
    assert resp.status_code == 202
    job = _poll(resp.json()["job_id"])
    assert job["status"] == "failed"


# ── ingest ────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_ingest_succeeds_and_returns_provenance():
    doc = "The speed of light in vacuum is approximately 299,792 kilometres per second."
    resp = httpx.post(f"{BASE}/capabilities", json=_rag_request("ingest", {"text": doc}), timeout=5.0)
    job_id = resp.json()["job_id"]
    job = _poll(job_id)

    assert job["status"] == "succeeded", f"Failed: {job.get('error')}"
    assert job["result"]["chunks_ingested"] >= 1
    assert "document_id" in job["result"]

    prov = job["provenance"]
    assert prov["backend_used"]
    assert isinstance(prov["tokens_in"], int) and prov["tokens_in"] >= 0
    assert prov["tokens_out"] == 0
    assert isinstance(prov["latency_ms"], int)
    assert prov["confidence"] == 1.0

    gates = job["gates"]
    assert gates["classification"] == "internal"
    assert gates["egress_decision"] == "allowed"


@pytest.mark.integration
def test_ingest_missing_text_fails():
    resp = httpx.post(f"{BASE}/capabilities", json=_rag_request("ingest", {}), timeout=5.0)
    job = _poll(resp.json()["job_id"])
    assert job["status"] == "failed"


@pytest.mark.integration
def test_ingest_is_idempotent():
    doc = "Idempotency test document. It contains a unique fact: the sky is blue."
    doc_id = f"idempotent-{uuid.uuid4().hex[:8]}"
    payload = {"text": doc, "document_id": doc_id}

    for _ in range(2):
        resp = httpx.post(f"{BASE}/capabilities", json=_rag_request("ingest", payload), timeout=5.0)
        job = _poll(resp.json()["job_id"])
        assert job["status"] == "succeeded"

    # Second ingest replaces the first — chunk count must remain consistent
    resp = httpx.post(f"{BASE}/capabilities", json=_rag_request("ingest", payload), timeout=5.0)
    job = _poll(resp.json()["job_id"])
    assert job["result"]["chunks_ingested"] >= 1


# ── query ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_query_returns_answer_and_sources():
    doc = "The Amazon river is the largest river by discharge volume in the world. It flows through South America."
    col = f"col-{uuid.uuid4().hex[:8]}"

    ingest_payload = _rag_request("ingest", {"text": doc})
    ingest_payload["payload"]["collection"] = col
    job = _poll(httpx.post(f"{BASE}/capabilities", json=ingest_payload, timeout=5.0).json()["job_id"])
    assert job["status"] == "succeeded"

    query_payload = _rag_request("query", {"question": "What is the Amazon?", "top_k": 3})
    query_payload["payload"]["collection"] = col
    job = _poll(httpx.post(f"{BASE}/capabilities", json=query_payload, timeout=5.0).json()["job_id"])

    assert job["status"] == "succeeded", f"Failed: {job.get('error')}"
    assert job["result"]["answer"]
    assert isinstance(job["result"]["sources"], list)
    assert len(job["result"]["sources"]) >= 1
    assert "score" in job["result"]["sources"][0]
    assert "content" in job["result"]["sources"][0]


@pytest.mark.integration
def test_query_full_provenance():
    col = f"col-{uuid.uuid4().hex[:8]}"
    doc = "Quantum entanglement is a physical phenomenon where particles become interconnected."
    ingest_req = _rag_request("ingest", {"text": doc})
    ingest_req["payload"]["collection"] = col
    _poll(httpx.post(f"{BASE}/capabilities", json=ingest_req, timeout=5.0).json()["job_id"])

    query_req = _rag_request("query", {"question": "What is quantum entanglement?", "top_k": 2})
    query_req["payload"]["collection"] = col
    job = _poll(httpx.post(f"{BASE}/capabilities", json=query_req, timeout=5.0).json()["job_id"])

    prov = job["provenance"]
    assert prov["backend_used"]
    assert isinstance(prov["tokens_in"], int)
    assert isinstance(prov["tokens_out"], int)
    assert isinstance(prov["latency_ms"], int)
    assert 0.0 <= prov["confidence"] <= 1.0

    gates = job["gates"]
    assert gates["egress_decision"] in ("allowed", "denied", "redacted_then_allowed")


@pytest.mark.integration
def test_query_missing_question_fails():
    resp = httpx.post(f"{BASE}/capabilities", json=_rag_request("query", {}), timeout=5.0)
    job = _poll(resp.json()["job_id"])
    assert job["status"] == "failed"


@pytest.mark.integration
def test_query_empty_collection_returns_answer():
    """Query against a collection with no documents must still return a result (not crash)."""
    empty_col = f"empty-{uuid.uuid4().hex[:8]}"
    query_req = _rag_request("query", {"question": "Does anything exist?", "top_k": 3})
    query_req["payload"]["collection"] = empty_col
    job = _poll(httpx.post(f"{BASE}/capabilities", json=query_req, timeout=5.0).json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["result"]["answer"]
    assert job["result"]["sources"] == []
