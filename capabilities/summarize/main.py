from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Summarize Capability")

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
MODEL = os.getenv("INFERENCE_MODEL", "stub-model")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-platform-dev")


class ExecuteRequest(BaseModel):
    capability: str
    operation: str
    context: dict[str, Any]
    payload: dict[str, Any]
    options: dict[str, Any]


@app.post("/execute")
async def execute(req: ExecuteRequest) -> dict[str, Any]:
    text = req.payload.get("text", "")
    if not text:
        raise HTTPException(status_code=422, detail="payload.text is required")

    classification = req.context.get("data_classification", "internal")

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json={
                "model": MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a concise summarizer. Summarize the given text in 2-3 sentences.",
                    },
                    {"role": "user", "content": text},
                ],
            },
            headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        )
        resp.raise_for_status()
        data = resp.json()

    latency_ms = int((time.monotonic() - start) * 1000)
    summary = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})

    return {
        "result": {"summary": summary},
        "provenance": {
            "backend_used": data.get("model", MODEL),
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
            "cost_usd": 0.0,
            "latency_ms": latency_ms,
            "confidence": 1.0,
        },
        "gates": {
            "classification": classification,
            "redactions_applied": 0,
            "egress_decision": "allowed",
        },
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
