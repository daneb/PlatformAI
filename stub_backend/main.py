"""
Mock OpenAI-compatible API for testing.

Returns canned responses from fixtures/<model>.json if present,
otherwise returns a default stub response. LiteLLM routes to this
in place of any real provider.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Stub Backend")

FIXTURES = Path(__file__).parent / "fixtures"


class _Message(BaseModel):
    role: str
    content: str


class _ChatRequest(BaseModel):
    model: str
    messages: list[_Message]
    stream: bool = False
    temperature: float = 1.0
    max_tokens: int = 512


@app.post("/v1/chat/completions")
async def chat_completions(req: _ChatRequest) -> dict[str, Any]:
    fixture = FIXTURES / f"{req.model}.json"
    if fixture.exists():
        return json.loads(fixture.read_text())

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "[STUB] Canned response from stub backend. No real model was called.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 42,
            "completion_tokens": 18,
            "total_tokens": 60,
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
