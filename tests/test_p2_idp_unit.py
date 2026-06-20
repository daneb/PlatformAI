"""Unit tests for P2 IDP — rule engine and stub classify/extract helpers. No docker required."""
from __future__ import annotations

from capabilities.idp.main import validate
from stub_backend.main import _Message, _stub_classify, _stub_extract_fields


# ── rule engine ──────────────────────────────────────────────────────────────

def test_validate_required_field_present():
    result = validate({"amount": "123.45"}, [{"field": "amount", "rule": "required"}])
    assert result == {"valid": True, "errors": []}


def test_validate_required_field_missing():
    result = validate({"amount": None}, [{"field": "amount", "rule": "required"}])
    assert result["valid"] is False
    assert "amount: required but missing" in result["errors"]


def test_validate_numeric_passes():
    result = validate({"amount": "$1,234.56"}, [{"field": "amount", "rule": "numeric"}])
    assert result["valid"] is True


def test_validate_numeric_fails():
    result = validate({"amount": "not-a-number"}, [{"field": "amount", "rule": "numeric"}])
    assert result["valid"] is False
    assert any("amount" in e for e in result["errors"])


def test_validate_regex_passes():
    rules = [{"field": "invoice_id", "rule": "regex", "pattern": r"^INV-\d+$"}]
    result = validate({"invoice_id": "INV-9001"}, rules)
    assert result["valid"] is True


def test_validate_regex_fails():
    rules = [{"field": "invoice_id", "rule": "regex", "pattern": r"^INV-\d+$"}]
    result = validate({"invoice_id": "9001"}, rules)
    assert result["valid"] is False


def test_validate_multiple_rules_accumulate_errors():
    rules = [
        {"field": "amount", "rule": "required"},
        {"field": "date", "rule": "required"},
    ]
    result = validate({"amount": None, "date": None}, rules)
    assert len(result["errors"]) == 2


def test_validate_no_rules_is_always_valid():
    assert validate({"anything": None}, []) == {"valid": True, "errors": []}


# ── stub classify ────────────────────────────────────────────────────────────

def _classify_messages(doc_text: str) -> list[_Message]:
    return [
        _Message(
            role="system",
            content="Classify this document into exactly one of: invoice, receipt, contract, other. "
            'Respond with JSON only: {"doc_type": "<type>"}',
        ),
        _Message(role="user", content=doc_text),
    ]


def test_stub_classify_detects_invoice():
    assert _stub_classify(_classify_messages("INVOICE\nVendor: Acme Corp\nAmount: $500")) == "invoice"


def test_stub_classify_detects_receipt():
    assert _stub_classify(_classify_messages("RECEIPT\nThank you for your purchase")) == "receipt"


def test_stub_classify_falls_back_to_last_type():
    assert _stub_classify(_classify_messages("Some unrelated rambling text")) == "other"


def test_stub_classify_returns_none_without_marker():
    messages = [_Message(role="system", content="You are a helpful assistant."), _Message(role="user", content="hi")]
    assert _stub_classify(messages) is None


# ── stub extract_fields ──────────────────────────────────────────────────────

def _extract_messages(doc_text: str, fields: list[str]) -> list[_Message]:
    return [
        _Message(
            role="system",
            content=f"Extract the following fields as JSON: {', '.join(fields)}. "
            "Use null for fields you cannot find. Respond with JSON only.",
        ),
        _Message(role="user", content=doc_text),
    ]


def test_stub_extract_fields_finds_values():
    doc = "Vendor: Acme Corp\nAmount: $1234.56\nDate: 2026-06-01"
    result = _stub_extract_fields(_extract_messages(doc, ["Vendor", "Amount", "Date"]))
    assert result == {"Vendor": "Acme Corp", "Amount": "$1234.56", "Date": "2026-06-01"}


def test_stub_extract_fields_missing_value_is_null():
    doc = "Vendor: Acme Corp"
    result = _stub_extract_fields(_extract_messages(doc, ["Vendor", "Amount"]))
    assert result == {"Vendor": "Acme Corp", "Amount": None}


def test_stub_extract_fields_returns_none_without_marker():
    messages = [_Message(role="system", content="You are a helpful assistant."), _Message(role="user", content="hi")]
    assert _stub_extract_fields(messages) is None
