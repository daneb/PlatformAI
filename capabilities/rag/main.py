from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from pgvector.asyncpg import register_vector
from pydantic import BaseModel

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
EMBED_MODEL = os.getenv("EMBED_MODEL", "embed-model")
GEN_MODEL = os.getenv("GEN_MODEL", "deepseek-v4-flash")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-platform-dev")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://platform:platform@postgres:5432/platform",
)

_EMBED_DIM = 768  # nomic-embed-text


@asynccontextmanager
async def lifespan(app: FastAPI):
    # register_vector introspects the 'vector' type at connect time, so the
    # extension must exist before the pool opens its first connection.
    bootstrap = await asyncpg.connect(DATABASE_URL)
    try:
        await bootstrap.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await bootstrap.close()

    app.state.pool = await asyncpg.create_pool(DATABASE_URL, init=register_vector)
    async with app.state.pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id          SERIAL PRIMARY KEY,
                collection  TEXT    NOT NULL DEFAULT 'default',
                document_id TEXT    NOT NULL,
                chunk_index INT     NOT NULL,
                content     TEXT    NOT NULL,
                embedding   VECTOR(768),
                created_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw
            ON rag_chunks USING hnsw (embedding vector_cosine_ops)
        """)
    yield
    await app.state.pool.close()


app = FastAPI(title="RAG Capability", lifespan=lifespan)


class ExecuteRequest(BaseModel):
    capability: str
    operation: str
    context: dict[str, Any]
    payload: dict[str, Any]
    options: dict[str, Any]


@app.post("/execute")
async def execute(req: ExecuteRequest) -> dict[str, Any]:
    if req.operation == "ingest":
        return await _ingest(req)
    if req.operation == "query":
        return await _query(req)
    raise HTTPException(status_code=422, detail=f"Unknown operation: {req.operation!r}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, max_chars: int = 512) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            current = ""
            for sentence in para.split(". "):
                candidate = (current + ". " + sentence).lstrip(". ")
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    if current:
                        chunks.append(current)
                    current = sentence
            if current:
                chunks.append(current)
    return chunks or [text[:max_chars]]


async def _embed(client: httpx.AsyncClient, texts: list[str]) -> list[np.ndarray]:
    resp = await client.post(
        f"{LITELLM_URL}/v1/embeddings",
        json={"model": EMBED_MODEL, "input": texts},
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return [np.array(item["embedding"], dtype="float32") for item in data["data"]]


# ── operations ───────────────────────────────────────────────────────────────

async def _ingest(req: ExecuteRequest) -> dict[str, Any]:
    text = req.payload.get("text", "")
    collection = req.payload.get("collection", "default")
    classification = req.context.get("data_classification", "internal")
    if not text:
        raise HTTPException(status_code=422, detail="payload.text is required for ingest")

    document_id = req.payload.get("document_id") or str(uuid.uuid4())
    chunks = _chunk_text(text)

    t0 = time.monotonic()
    async with httpx.AsyncClient() as client:
        embeddings = await _embed(client, chunks)

    async with app.state.pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM rag_chunks WHERE collection = $1 AND document_id = $2",
            collection, document_id,
        )
        await conn.executemany(
            "INSERT INTO rag_chunks (collection, document_id, chunk_index, content, embedding) "
            "VALUES ($1, $2, $3, $4, $5)",
            [
                (collection, document_id, idx, chunk, emb)
                for idx, (chunk, emb) in enumerate(zip(chunks, embeddings))
            ],
        )

    return {
        "result": {"document_id": document_id, "chunks_ingested": len(chunks)},
        "provenance": {
            "backend_used": EMBED_MODEL,
            "tokens_in": sum(len(c.split()) for c in chunks),
            "tokens_out": 0,
            "cost_usd": 0.0,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "confidence": 1.0,
        },
        "gates": {
            "classification": classification,
            "redactions_applied": 0,
            "egress_decision": "allowed",
        },
    }


async def _query(req: ExecuteRequest) -> dict[str, Any]:
    question = req.payload.get("question", "")
    collection = req.payload.get("collection", "default")
    top_k = int(req.payload.get("top_k", 5))
    classification = req.context.get("data_classification", "internal")
    if not question:
        raise HTTPException(status_code=422, detail="payload.question is required for query")

    t0 = time.monotonic()
    async with httpx.AsyncClient() as client:
        [q_vec] = await _embed(client, [question])

        async with app.state.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT content, 1 - (embedding <=> $1) AS score
                FROM   rag_chunks
                WHERE  collection = $2
                ORDER  BY embedding <=> $1
                LIMIT  $3
                """,
                q_vec, collection, top_k,
            )

        context_text = (
            "\n\n---\n\n".join(r["content"] for r in rows)
            if rows
            else "[No relevant documents found in this collection]"
        )

        gen_resp = await client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            json={
                "model": GEN_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Answer the user's question using only the context below. "
                            "If the context does not contain the answer, say 'I don't know'.\n\n"
                            f"Context:\n{context_text}"
                        ),
                    },
                    {"role": "user", "content": question},
                ],
            },
            headers={"Authorization": f"Bearer {LITELLM_KEY}"},
            timeout=60.0,
        )
        gen_resp.raise_for_status()
        gen_data = gen_resp.json()

    answer = gen_data["choices"][0]["message"]["content"]
    usage = gen_data.get("usage", {})
    sources = [{"content": r["content"], "score": float(r["score"])} for r in rows]

    return {
        "result": {"answer": answer, "sources": sources},
        "provenance": {
            "backend_used": gen_data.get("model", GEN_MODEL),
            "tokens_in": usage.get("prompt_tokens", 0),
            "tokens_out": usage.get("completion_tokens", 0),
            "cost_usd": 0.0,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "confidence": max(0.0, float(rows[0]["score"])) if rows else 0.0,
        },
        "gates": {
            "classification": classification,
            "redactions_applied": 0,
            "egress_decision": "allowed",
        },
    }
