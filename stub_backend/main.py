"""
Mock OpenAI-compatible API for testing.

For chat completions: returns a canned fixture from fixtures/<model>.json if one
exists, otherwise echoes the context section back as the answer (so RAG eval
assertions can find the facts that were ingested).

For embeddings: returns deterministic 768-dim unit vectors seeded from a hash of
each input string, giving unique and repeatable vectors without any real model.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Stub Backend")

FIXTURES = Path(__file__).parent / "fixtures"
_EMBED_DIM = 768


# ── request models ────────────────────────────────────────────────────────────

class _Message(BaseModel):
    role: str
    content: str | list[Any]

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return " ".join(
            part.get("text", "")
            for part in self.content
            if isinstance(part, dict) and part.get("type") == "text"
        )


class _ChatRequest(BaseModel):
    model: str
    messages: list[_Message]
    stream: bool = False
    temperature: float = 1.0
    max_tokens: int = 512


class _EmbedRequest(BaseModel):
    model: str
    input: list[str] | str


# ── helpers ───────────────────────────────────────────────────────────────────

def _stub_embedding(text: str) -> list[float]:
    """Deterministic pseudo-random unit vector seeded from MD5(text)."""
    seed = struct.unpack("<I", hashlib.md5(text.encode()).digest()[:4])[0]
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(_EMBED_DIM)]
    norm = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / norm for x in vec]


def _rag_answer_from_context(messages: list[_Message]) -> str:
    """If the system prompt contains a Context: block, return it as the answer.

    This lets the eval harness assert that ingested facts appear in the answer
    without calling any real model.
    """
    for msg in messages:
        text = msg.text()
        if msg.role == "system" and "Context:" in text:
            ctx_start = text.index("Context:") + len("Context:")
            return text[ctx_start:].strip()[:600]
    return "[STUB] Canned response from stub backend. No real model was called."


def _stub_classify(messages: list[_Message]) -> str | None:
    """Deterministic doc-type classification: keyword match against the doc text."""
    system = next((m.text() for m in messages if m.role == "system"), "")
    user = next((m.text() for m in messages if m.role == "user"), "")
    match = re.search(r"one of:\s*(.+?)\.", system)
    if not match:
        return None
    doc_types = [t.strip() for t in match.group(1).split(",")]
    lower_text = user.lower()
    for doc_type in doc_types:
        if doc_type.lower() != "other" and doc_type.lower() in lower_text:
            return doc_type
    return doc_types[-1] if doc_types else "other"


def _stub_extract_fields(messages: list[_Message]) -> dict[str, Any] | None:
    """Deterministic field extraction: regex 'field: value' lookup in the doc text."""
    system = next((m.text() for m in messages if m.role == "system"), "")
    user = next((m.text() for m in messages if m.role == "user"), "")
    match = re.search(r"Extract the following fields as JSON:\s*(.+?)\.", system)
    if not match:
        return None
    fields = [f.strip() for f in match.group(1).split(",")]
    result: dict[str, Any] = {}
    for field in fields:
        field_match = re.search(rf"{re.escape(field)}\s*[:\-]\s*(.+)", user, re.IGNORECASE)
        result[field] = field_match.group(1).strip().splitlines()[0] if field_match else None
    return result


def _stub_vision_detect(messages: list[_Message]) -> str:
    """Return all requested labels as present — deterministic stub for detect operation."""
    user_text = next((m.text() for m in messages if m.role == "user"), "")
    match = re.search(r"Detect these labels:\s*(.+)", user_text)
    labels = [l.strip() for l in match.group(1).split(",")] if match else []
    return json.dumps({"detections": [{"label": l, "present": True, "confidence": 0.9} for l in labels]})


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(req: _ChatRequest) -> dict[str, Any]:
    fixture = FIXTURES / f"{req.model}.json"
    if fixture.exists():
        return json.loads(fixture.read_text())

    system = next((m.text() for m in req.messages if m.role == "system"), "")
    if "Classify this document into exactly one of:" in system:
        content = json.dumps({"doc_type": _stub_classify(req.messages) or "other"})
    elif "Extract the following fields as JSON:" in system:
        content = json.dumps(_stub_extract_fields(req.messages) or {})
    elif "Describe the image sent by the user" in system:
        content = "A simple white test image containing rendered text on a plain background."
    elif "Detect objects in the image" in system:
        content = _stub_vision_detect(req.messages)
    elif "Answer the user's question about the image" in system:
        content = "Stub answer to your question about the image."
    else:
        content = _rag_answer_from_context(req.messages)

    prompt_tokens = sum(len(m.text().split()) for m in req.messages)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(content.split()),
            "total_tokens": prompt_tokens + len(content.split()),
        },
    }


@app.post("/v1/embeddings")
async def embeddings(req: _EmbedRequest) -> dict[str, Any]:
    texts = [req.input] if isinstance(req.input, str) else req.input
    data = [
        {"object": "embedding", "index": i, "embedding": _stub_embedding(text)}
        for i, text in enumerate(texts)
    ]
    return {
        "object": "list",
        "data": data,
        "model": req.model,
        "usage": {"prompt_tokens": sum(len(t.split()) for t in texts), "total_tokens": sum(len(t.split()) for t in texts)},
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
