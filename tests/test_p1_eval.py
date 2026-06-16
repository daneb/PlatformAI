"""
P1 eval harness integration test — runs the golden Q&A set against the live stack.

Each case: ingest the document → query with the question → assert expected facts
are present in the answer. Uses a rubric-based scorer (fact substring matching).

Requires the full docker-compose stack:
  docker compose up -d
  pytest tests/test_p1_eval.py -m integration -v
"""
from __future__ import annotations

import time
import uuid

import httpx
import pytest

from tests.eval.harness import evaluate, load_golden

BASE = "http://localhost:8000"


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


def _submit(operation: str, payload: dict, collection: str) -> str:
    body = {
        "capability": "rag",
        "operation": operation,
        "context": {"tenant_id": "eval", "principal": "svc-eval"},
        "payload": {**payload, "collection": collection},
        "options": {"mode": "async"},
    }
    resp = httpx.post(f"{BASE}/capabilities", json=body, timeout=10.0)
    resp.raise_for_status()
    return resp.json()["job_id"]


@pytest.mark.integration
def test_golden_set_passes():
    """All golden Q&A cases must score 1.0 (all expected facts found in the answer).

    Each case uses its own isolated collection so the retrieval step always
    returns the single document that was ingested, regardless of embedding
    similarity (the stub backend produces non-semantic embeddings).
    """
    golden = load_golden()
    failures: list[dict] = []

    for item in golden:
        # Isolated collection per case: guarantees the correct doc is retrieved
        collection = f"eval-{uuid.uuid4().hex[:8]}"

        job = _poll(_submit("ingest", {"text": item["doc"]}, collection))
        assert job["status"] == "succeeded", f"Ingest failed: {job.get('error')}"

        job = _poll(_submit("query", {"question": item["question"], "top_k": 3}, collection))
        assert job["status"] == "succeeded", f"Query failed: {job.get('error')}"

        answer = job["result"]["answer"]
        result = evaluate(answer, item["expected_facts"])

        if result["failed"]:
            failures.append({
                "question": item["question"],
                "answer": answer[:200],
                "missing_facts": result["failed"],
                "score": result["score"],
            })

    assert not failures, (
        f"{len(failures)}/{len(golden)} eval cases failed:\n"
        + "\n".join(
            f"  Q: {f['question']!r}\n"
            f"  A: {f['answer']!r}\n"
            f"  Missing: {f['missing_facts']}"
            for f in failures
        )
    )
