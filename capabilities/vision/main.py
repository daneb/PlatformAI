from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import boto3
import httpx
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
GEN_MODEL = os.getenv("GEN_MODEL", "deepseek-v4-flash")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-platform-dev")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "platform")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "platform123")

_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

app = FastAPI(title="Vision Capability")


class ExecuteRequest(BaseModel):
    capability: str
    operation: str
    context: dict[str, Any]
    payload: dict[str, Any]
    options: dict[str, Any]


@app.post("/execute")
async def execute(req: ExecuteRequest) -> dict[str, Any]:
    if req.operation == "describe":
        return await _describe(req)
    if req.operation == "detect":
        return await _detect(req)
    if req.operation == "ask":
        return await _ask(req)
    raise HTTPException(status_code=422, detail=f"Unknown operation: {req.operation!r}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def _load_image_b64(payload: dict[str, Any]) -> tuple[str, str]:
    object_ref = payload.get("object_ref", "")
    if not object_ref.startswith("s3://"):
        raise HTTPException(status_code=422, detail="payload.object_ref must be 's3://bucket/key'")
    bucket, _, key = object_ref[len("s3://"):].partition("/")
    try:
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise HTTPException(status_code=404, detail=f"object_ref not found: {object_ref}") from exc
    data = obj["Body"].read()
    ext = key.lower().rsplit(".", 1)[-1] if "." in key else "png"
    mime = _MIME.get(ext, "image/png")
    return base64.b64encode(data).decode(), mime


def _gates(req: ExecuteRequest) -> dict[str, Any]:
    return {
        "classification": req.context.get("data_classification", "internal"),
        "redactions_applied": 0,
        "egress_decision": "allowed",
    }


async def _call_litellm(messages: list[dict], t0: float) -> tuple[str, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_KEY}"},
            json={"model": GEN_MODEL, "messages": messages, "max_tokens": 512},
        )
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return content, {
        "backend_used": GEN_MODEL,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
        "cost_usd": 0.0,
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "confidence": 1.0,
    }


def _image_part(b64: str, mime: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


# ── operations ───────────────────────────────────────────────────────────────

async def _describe(req: ExecuteRequest) -> dict[str, Any]:
    t0 = time.monotonic()
    b64, mime = _load_image_b64(req.payload)
    messages = [
        {"role": "system", "content": "Describe the image sent by the user in detail. Be concise and factual."},
        {"role": "user", "content": [_image_part(b64, mime), {"type": "text", "text": "Describe this image."}]},
    ]
    content, provenance = await _call_litellm(messages, t0)
    return {"result": {"description": content}, "provenance": provenance, "gates": _gates(req)}


async def _detect(req: ExecuteRequest) -> dict[str, Any]:
    t0 = time.monotonic()
    labels = req.payload.get("labels", [])
    if not labels:
        raise HTTPException(status_code=422, detail="payload.labels is required for detect")
    b64, mime = _load_image_b64(req.payload)
    messages = [
        {
            "role": "system",
            "content": 'Detect objects in the image. Return JSON only: {"detections": [{"label": "str", "present": true, "confidence": 0.0}]}',
        },
        {
            "role": "user",
            "content": [
                _image_part(b64, mime),
                {"type": "text", "text": f"Detect these labels: {', '.join(labels)}"},
            ],
        },
    ]
    content, provenance = await _call_litellm(messages, t0)
    try:
        detections = json.loads(content).get("detections", [])
    except json.JSONDecodeError:
        detections = [{"label": l, "present": False, "confidence": 0.0} for l in labels]
    return {"result": {"detections": detections}, "provenance": provenance, "gates": _gates(req)}


async def _ask(req: ExecuteRequest) -> dict[str, Any]:
    t0 = time.monotonic()
    question = req.payload.get("question", "")
    if not question:
        raise HTTPException(status_code=422, detail="payload.question is required for ask")
    b64, mime = _load_image_b64(req.payload)
    messages = [
        {"role": "system", "content": "Answer the user's question about the image."},
        {"role": "user", "content": [_image_part(b64, mime), {"type": "text", "text": question}]},
    ]
    content, provenance = await _call_litellm(messages, t0)
    return {"result": {"answer": content}, "provenance": provenance, "gates": _gates(req)}
