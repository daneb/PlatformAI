# AI Platform — Project Rules for Claude Code

Full specification: [`AI-Platform-SPEC.md`](AI-Platform-SPEC.md)

---

## Build phases — work sequentially, never skip ahead

| Phase | Scope | Gate before next phase |
|---|---|---|
| **P0** | Walking skeleton — gateway + LiteLLM broker + stub provider + full envelope + provenance | End-to-end request flows; all tests green |
| **P1** | RAG — pgvector + local embeddings + remote generation + eval harness | Eval harness running; golden set passing |
| **P2** | OCR + IDP pipeline — Tesseract → extract → validate DAG | Contract tests for each step; pipeline integration test |
| **P3** | Vision + Anomaly detection | Capability tests; local anomaly model fits in RAM budget |
| **P4** | Security hardening — egress gate, Presidio, OPA, audit log, adversarial tests | Fail-closed denial test passing; no sensitive data in egress logs |

P5 (real-AWS smoke test) is **dropped** — LocalStack parity is the target.

---

## Architecture rules — non-negotiable

- **Uniform envelope.** Every request and response must match the contract in spec §7. No shortcutting the envelope for "simple" cases.
- **Broker-only inference.** No capability service calls a model provider directly. All inference goes through LiteLLM.
- **Async by default.** Every capability uses submit-job → poll/callback. Sync is sugar over async, not a separate code path.
- **Provenance on every response.** `backend_used`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`, `confidence`, and `gates` are mandatory fields — never omit them.
- **Fail-closed egress.** When classification is uncertain, deny. `confidential` and `restricted` data never egresses to a remote provider, regardless of redaction confidence.
- **One local model hot at a time.** Lazy-load on first use; idle-unload. Never co-host a local vision model and a local LLM.

---

## Tenancy

Single-tenant for this build. The `tenant_id` field **must be present** in the envelope (models the multi-tenant seam) but infra-level isolation is not enforced. Do not add multi-tenant enforcement complexity unless explicitly requested.

---

## Testing rules

- **No paid API calls in tests — ever.** Use the record/replay stub provider for all remote-backend responses.
- **Record once, replay always.** Real DeepSeek responses are recorded to a `tests/fixtures/` directory and replayed in CI. Never regenerate fixtures in a CI run.
- **Eval harness from P1.** Use a proper eval framework for generative outputs (RAG answers, IDP extractions) — rubric-based or LLM-judge with cached responses. Golden-file snapshots alone are not sufficient for non-deterministic steps.
- **LocalStack for all AWS-shaped infra.** S3, SQS, SNS tests run against LocalStack Community. No real AWS credentials in the repo or CI.
- **Manual smoke test each phase.** Before marking a phase complete, manually exercise the happy path and at least one error/denial path.

---

## Model & inference rules

| Use | Model ID |
|---|---|
| Workhorse (default) | `deepseek-v4-flash` |
| Hard reasoning | `deepseek-v4-pro` |
| Embeddings (local) | `nomic-embed-text` via Ollama |

- Pin model IDs explicitly in the registry. **Never use the deprecated aliases** `deepseek-chat` or `deepseek-reasoner` (retired 2026-07-24).
- `backend_hint` in the request envelope is advisory only; routing policy can override it.

---

## RAG UX (resolved)

Batch async: `POST /rag/query` → `202 + job_id` → `GET /jobs/{id}` until `succeeded`. Streaming generation (SSE) is out of P1 scope.

---

## Security rules

- PII redaction via **Presidio** must run before any remote egress — no exceptions, including dev shortcuts.
- **OPA/Rego** policies are the source of truth for egress decisions. Policy files live under `policy/` and are tested independently.
- Secrets via **SOPS + age**. Never hardcode credentials; never commit `.env` files with real values.
- Log hashes and classification labels only. Never log raw payloads, even at debug level.

---

## Tech stack

| Concern | Choice |
|---|---|
| Services | Python + FastAPI |
| Containers | docker-compose (OrbStack) |
| Model gateway | LiteLLM |
| Vector store | Postgres + pgvector |
| Object store | MinIO (S3 API) |
| AWS-shaped infra | LocalStack Community |
| IaC | Terraform (LocalStack target) |
| Identity | Keycloak (OIDC) |
| Secrets | SOPS + age |
| Guardrails | Presidio (PII) + OPA/Rego (policy) |
| Observability | OpenTelemetry + Grafana/Loki/Tempo/Prometheus |

---

## Code style

- No comments unless the WHY is non-obvious (hidden constraint, workaround, subtle invariant).
- No docstrings for self-explanatory functions.
- Prefer editing existing files to creating new ones.
- No half-finished implementations — each phase ships something runnable.
