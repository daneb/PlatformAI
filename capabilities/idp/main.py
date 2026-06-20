from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
GEN_MODEL = os.getenv("GEN_MODEL", "deepseek-v4-flash")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-platform-dev")
OCR_URL = os.getenv("OCR_URL", "http://capability-ocr:8004")

_DOC_TYPES = ["invoice", "receipt", "contract", "other"]

app = FastAPI(title="IDP Capability")


class ExecuteRequest(BaseModel):
    capability: str
    operation: str
    context: dict[str, Any]
    payload: dict[str, Any]
    options: dict[str, Any]


@app.post("/execute")
async def execute(req: ExecuteRequest) -> dict[str, Any]:
    if req.operation != "process":
        raise HTTPException(status_code=422, detail=f"Unknown operation: {req.operation!r}")
    return await _process(req)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── rule engine ──────────────────────────────────────────────────────────────

def validate(fields: dict[str, Any], ruleset: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    for rule in ruleset:
        field = rule.get("field")
        kind = rule.get("rule")
        value = fields.get(field)
        if kind == "required":
            if value in (None, ""):
                errors.append(f"{field}: required but missing")
        elif kind == "numeric":
            if value is not None and not re.fullmatch(r"[\$£€]?\s*-?[0-9,]+(\.[0-9]+)?", str(value).strip()):
                errors.append(f"{field}: expected numeric value, got {value!r}")
        elif kind == "regex":
            pattern = rule.get("pattern", "")
            if value is not None and not re.search(pattern, str(value)):
                errors.append(f"{field}: does not match pattern {pattern!r}")
    return {"valid": not errors, "errors": errors}


# ── pipeline steps ───────────────────────────────────────────────────────────

async def _ocr_extract_layout(client: httpx.AsyncClient, req: ExecuteRequest) -> str:
    resp = await client.post(
        f"{OCR_URL}/execute",
        json={
            "capability": "ocr",
            "operation": "extract_layout",
            "context": req.context,
            "payload": {"object_ref": req.payload.get("object_ref")},
            "options": req.options,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["result"]["text"]


async def _classify(client: httpx.AsyncClient, text: str) -> dict[str, Any]:
    resp = await client.post(
        f"{LITELLM_URL}/v1/chat/completions",
        json={
            "model": GEN_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Classify this document into exactly one of: {', '.join(_DOC_TYPES)}. "
                        'Respond with JSON only: {"doc_type": "<type>"}'
                    ),
                },
                {"role": "user", "content": text},
            ],
        },
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


async def _extract_fields(client: httpx.AsyncClient, text: str, schema: list[str]) -> dict[str, Any]:
    resp = await client.post(
        f"{LITELLM_URL}/v1/chat/completions",
        json={
            "model": GEN_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Extract the following fields as JSON: {', '.join(schema)}. "
                        "Use null for fields you cannot find. Respond with JSON only."
                    ),
                },
                {"role": "user", "content": text},
            ],
        },
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


# ── orchestration ────────────────────────────────────────────────────────────

async def _process(req: ExecuteRequest) -> dict[str, Any]:
    schema = req.payload.get("schema", [])
    ruleset = req.payload.get("ruleset", [])
    classification = req.context.get("data_classification", "internal")
    if not req.payload.get("object_ref"):
        raise HTTPException(status_code=422, detail="payload.object_ref is required for process")
    if not schema:
        raise HTTPException(status_code=422, detail="payload.schema is required for process")

    t0 = time.monotonic()
    tokens_in = 0
    tokens_out = 0

    async with httpx.AsyncClient() as client:
        text = await _ocr_extract_layout(client, req)

        classify_resp = await _classify(client, text)
        doc_type = json.loads(classify_resp["choices"][0]["message"]["content"]).get("doc_type", "other")
        usage = classify_resp.get("usage", {})
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)

        extract_resp = await _extract_fields(client, text, schema)
        fields = json.loads(extract_resp["choices"][0]["message"]["content"])
        usage = extract_resp.get("usage", {})
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)

    validation = validate(fields, ruleset)

    return {
        "result": {"doc_type": doc_type, "fields": fields, "validation": validation},
        "provenance": {
            "backend_used": GEN_MODEL,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": 0.0,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "confidence": 1.0 if validation["valid"] else 0.5,
        },
        "gates": {
            "classification": classification,
            "redactions_applied": 0,
            "egress_decision": "allowed",
        },
    }
