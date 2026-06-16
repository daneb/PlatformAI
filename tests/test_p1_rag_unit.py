"""Unit tests for P1 RAG — no docker required."""
from __future__ import annotations

import hashlib
import struct

import pytest

from capabilities.rag.main import _chunk_text
from tests.eval.harness import evaluate, load_golden


# ── chunking ──────────────────────────────────────────────────────────────────

def test_chunk_short_text_is_single_chunk():
    chunks = _chunk_text("Hello world.")
    assert chunks == ["Hello world."]


def test_chunk_respects_paragraph_boundaries():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = _chunk_text(text)
    assert len(chunks) == 3


def test_chunk_long_paragraph_splits_on_sentences():
    sentence = "This is a sentence. "
    long_para = sentence * 40  # ~800 chars, well over 512
    chunks = _chunk_text(long_para)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 512


def test_chunk_empty_string_returns_single_empty_chunk():
    chunks = _chunk_text("")
    assert len(chunks) == 1


def test_chunk_preserves_content():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
    chunks = _chunk_text(text)
    combined = " ".join(chunks)
    assert "Paragraph one" in combined
    assert "Paragraph two" in combined
    assert "Paragraph three" in combined


# ── eval harness ──────────────────────────────────────────────────────────────

def test_evaluate_all_facts_present():
    result = evaluate("The capital of France is Paris", ["Paris", "France"])
    assert result["score"] == 1.0
    assert result["failed"] == []


def test_evaluate_partial_facts():
    result = evaluate("The capital of France is Paris", ["Paris", "Berlin"])
    assert result["score"] == 0.5
    assert "Berlin" in result["failed"]
    assert "Paris" in result["passed"]


def test_evaluate_no_facts_present():
    result = evaluate("I don't know", ["Paris"])
    assert result["score"] == 0.0
    assert result["failed"] == ["Paris"]


def test_evaluate_is_case_insensitive():
    result = evaluate("paris is the capital", ["Paris"])
    assert result["score"] == 1.0


def test_evaluate_empty_facts():
    result = evaluate("any answer", [])
    assert result["score"] == 1.0


def test_golden_set_loads():
    golden = load_golden()
    assert len(golden) >= 5
    for item in golden:
        assert "doc" in item
        assert "question" in item
        assert "expected_facts" in item
        assert isinstance(item["expected_facts"], list)


# ── stub embedding determinism ────────────────────────────────────────────────

def _stub_embedding(text: str, dim: int = 768) -> list[float]:
    """Mirror of stub_backend logic for unit verification."""
    import random
    seed = struct.unpack("<I", hashlib.md5(text.encode()).digest()[:4])[0]
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


def test_stub_embedding_is_deterministic():
    e1 = _stub_embedding("hello")
    e2 = _stub_embedding("hello")
    assert e1 == e2


def test_stub_embedding_differs_by_input():
    e1 = _stub_embedding("hello")
    e2 = _stub_embedding("world")
    assert e1 != e2


def test_stub_embedding_is_unit_vector():
    import math
    e = _stub_embedding("test text")
    norm = math.sqrt(sum(x * x for x in e))
    assert abs(norm - 1.0) < 1e-5


def test_stub_embedding_has_correct_dim():
    e = _stub_embedding("test")
    assert len(e) == 768
