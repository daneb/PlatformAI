"""
Rubric-based eval harness for RAG golden-set evaluation.

Scoring: each expected fact is checked for case-insensitive substring presence
in the answer. A case scores 1.0 only if all facts are found.
"""
from __future__ import annotations

import json
from pathlib import Path


def evaluate(answer: str, expected_facts: list[str]) -> dict:
    """Return pass/fail breakdown and a 0–1 score for a single answer."""
    lower = answer.lower()
    passed = [f for f in expected_facts if f.lower() in lower]
    failed = [f for f in expected_facts if f.lower() not in lower]
    return {
        "passed": passed,
        "failed": failed,
        "score": len(passed) / len(expected_facts) if expected_facts else 1.0,
    }


def load_golden() -> list[dict]:
    path = Path(__file__).parent / "golden.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
