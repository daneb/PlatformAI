from __future__ import annotations

import os
from typing import Any

import httpx

OPA_URL = os.getenv("OPA_URL", "http://opa:8181")

# Capabilities whose entire execution is on-premises — no remote inference call.
_LOCAL = frozenset({"ocr", "anomaly"})

# High-precision PII entity types only.  Excludes URL (corrupts S3 URIs),
# ORGANIZATION (matches acronyms), US_DRIVER_LICENSE (matches s3:// prefixes),
# PERSON (NER false-positives on proper nouns), and other low-precision types.
_PII_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "IP_ADDRESS",
]

# Lazy-initialised Presidio engines (avoid loading spaCy on import).
_analyzer: Any = None
_anonymizer: Any = None


def _get_engines() -> tuple[Any, Any]:
    global _analyzer, _anonymizer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine
        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        _anonymizer = AnonymizerEngine()
    return _analyzer, _anonymizer


def _redact_value(v: Any) -> tuple[Any, int]:
    analyzer, anonymizer = _get_engines()
    if isinstance(v, str):
        results = analyzer.analyze(text=v, language="en", entities=_PII_ENTITIES)
        if results:
            anon = anonymizer.anonymize(text=v, analyzer_results=results)
            return anon.text, len(results)
        return v, 0
    if isinstance(v, dict):
        total = 0
        out: dict[str, Any] = {}
        for k, vv in v.items():
            rv, n = _redact_value(vv)
            out[k] = rv
            total += n
        return out, total
    if isinstance(v, list):
        total = 0
        out_list: list[Any] = []
        for item in v:
            ri, n = _redact_value(item)
            out_list.append(ri)
            total += n
        return out_list, total
    return v, 0


def _redact_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    total = 0
    out: dict[str, Any] = {}
    for k, v in payload.items():
        rv, n = _redact_value(v)
        out[k] = rv
        total += n
    return out, total


def _query_opa(capability: str, classification: str, residency: str) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{OPA_URL}/v1/data/platform/egress",
                json={"input": {"capability": capability, "classification": classification, "residency": residency}},
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
            allow = bool(result.get("allow", False))
            deny_reason = str(result.get("deny_reason", "policy:default-deny" if not allow else ""))
            return allow, deny_reason
    except Exception as exc:
        # Fail closed: OPA unreachable means deny.
        return False, f"opa_unavailable:{exc}"


def evaluate(
    capability: str,
    classification: str,
    residency: str,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any], int, str]:
    """
    Run the egress gate.

    Returns (decision, redacted_payload, redactions_applied, deny_reason) where
    decision is one of: "local" | "allowed" | "redacted_then_allowed" | "denied".
    """
    if capability in _LOCAL:
        return "local", payload, 0, ""

    allow, deny_reason = _query_opa(capability, classification, residency)
    if not allow:
        return "denied", payload, 0, deny_reason

    redacted, n = _redact_payload(payload)
    decision = "redacted_then_allowed" if n > 0 else "allowed"
    return decision, redacted, n, ""
