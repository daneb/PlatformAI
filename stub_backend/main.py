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
    content: str


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
        if msg.role == "system" and "Context:" in msg.content:
            ctx_start = msg.content.index("Context:") + len("Context:")
            return msg.content[ctx_start:].strip()[:600]
    return "[STUB] Canned response from stub backend. No real model was called."


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(req: _ChatRequest) -> dict[str, Any]:
    fixture = FIXTURES / f"{req.model}.json"
    if fixture.exists():
        return json.loads(fixture.read_text())

    content = _rag_answer_from_context(req.messages)
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
            "prompt_tokens": sum(len(m.content.split()) for m in req.messages),
            "completion_tokens": len(content.split()),
            "total_tokens": sum(len(m.content.split()) for m in req.messages) + len(content.split()),
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
