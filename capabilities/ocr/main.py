from __future__ import annotations

import io
import os
import time
from typing import Any

import boto3
import pytesseract
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "platform")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "platform123")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "documents")

app = FastAPI(title="OCR Capability")


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


@app.on_event("startup")
async def _ensure_bucket() -> None:
    s3 = _s3_client()
    try:
        s3.head_bucket(Bucket=MINIO_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=MINIO_BUCKET)


class ExecuteRequest(BaseModel):
    capability: str
    operation: str
    context: dict[str, Any]
    payload: dict[str, Any]
    options: dict[str, Any]


@app.post("/execute")
async def execute(req: ExecuteRequest) -> dict[str, Any]:
    if req.operation == "extract_text":
        return _extract_text(req)
    if req.operation == "extract_layout":
        return _extract_layout(req)
    raise HTTPException(status_code=422, detail=f"Unknown operation: {req.operation!r}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_image(payload: dict[str, Any]) -> Image.Image:
    object_ref = payload.get("object_ref", "")
    if not object_ref.startswith("s3://"):
        raise HTTPException(status_code=422, detail="payload.object_ref must be 's3://bucket/key'")

    bucket, _, key = object_ref[len("s3://"):].partition("/")
    try:
        obj = _s3_client().get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise HTTPException(status_code=404, detail=f"object_ref not found: {object_ref}") from exc

    return Image.open(io.BytesIO(obj["Body"].read()))


def _gates(req: ExecuteRequest) -> dict[str, Any]:
    return {
        "classification": req.context.get("data_classification", "internal"),
        "redactions_applied": 0,
        "egress_decision": "allowed",  # local-only — Tesseract never egresses
    }


# ── operations ───────────────────────────────────────────────────────────────

def _extract_text(req: ExecuteRequest) -> dict[str, Any]:
    image = _load_image(req.payload)
    t0 = time.monotonic()
    text = pytesseract.image_to_string(image)
    latency_ms = int((time.monotonic() - t0) * 1000)

    return {
        "result": {"text": text.strip()},
        "provenance": {
            "backend_used": "tesseract-ocr",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "latency_ms": latency_ms,
            "confidence": 1.0,
        },
        "gates": _gates(req),
    }


def _extract_layout(req: ExecuteRequest) -> dict[str, Any]:
    image = _load_image(req.payload)
    t0 = time.monotonic()
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    latency_ms = int((time.monotonic() - t0) * 1000)

    indices = [i for i in range(len(data["text"])) if data["text"][i].strip()]
    words = [
        {
            "text": data["text"][i],
            "confidence": float(data["conf"][i]),
            "left": data["left"][i],
            "top": data["top"][i],
            "width": data["width"][i],
            "height": data["height"][i],
        }
        for i in indices
    ]

    # Reconstruct line breaks (image_to_data flattens words; block/par/line_num group them
    # back into the document's visual lines, which downstream field extraction relies on).
    lines: dict[tuple[int, int, int], list[str]] = {}
    for i in indices:
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(data["text"][i])
    full_text = "\n".join(" ".join(line_words) for line_words in lines.values())

    avg_conf = sum(w["confidence"] for w in words) / len(words) / 100.0 if words else 0.0

    return {
        "result": {"text": full_text, "words": words},
        "provenance": {
            "backend_used": "tesseract-ocr",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "latency_ms": latency_ms,
            "confidence": max(0.0, avg_conf),
        },
        "gates": _gates(req),
    }
