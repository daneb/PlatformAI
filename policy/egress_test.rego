package platform.egress_test

import rego.v1
import data.platform.egress

# ── Local capabilities ─────────────────────────────────────────────────────────

test_ocr_allowed_even_when_confidential if {
	egress.allow with input as {"capability": "ocr", "classification": "confidential", "residency": "any"}
}

test_anomaly_allowed_even_when_restricted if {
	egress.allow with input as {"capability": "anomaly", "classification": "restricted", "residency": "on_prem_only"}
}

# ── Remote capabilities — non-sensitive ────────────────────────────────────────

test_public_remote_allowed if {
	egress.allow with input as {"capability": "summarize", "classification": "public", "residency": "any"}
}

test_internal_remote_allowed if {
	egress.allow with input as {"capability": "rag", "classification": "internal", "residency": "any"}
}

# ── Remote capabilities — sensitive ───────────────────────────────────────────

test_confidential_remote_denied if {
	not egress.allow with input as {"capability": "vision", "classification": "confidential", "residency": "any"}
}

test_restricted_remote_denied if {
	not egress.allow with input as {"capability": "idp", "classification": "restricted", "residency": "any"}
}

test_on_prem_only_denied if {
	not egress.allow with input as {"capability": "summarize", "classification": "internal", "residency": "on_prem_only"}
}

# ── deny_reason strings ────────────────────────────────────────────────────────

test_deny_reason_confidential if {
	egress.deny_reason == "classification:confidential — data may not egress to a remote provider" with input as {
		"capability": "rag",
		"classification": "confidential",
		"residency": "any",
	}
}

test_deny_reason_restricted if {
	egress.deny_reason == "classification:restricted — data may not egress to a remote provider" with input as {
		"capability": "rag",
		"classification": "restricted",
		"residency": "any",
	}
}

test_deny_reason_on_prem_only if {
	egress.deny_reason == "residency:on_prem_only — remote egress is prohibited for this request" with input as {
		"capability": "summarize",
		"classification": "internal",
		"residency": "on_prem_only",
	}
}

test_deny_reason_empty_when_allowed if {
	egress.deny_reason == "" with input as {
		"capability": "rag",
		"classification": "internal",
		"residency": "any",
	}
}
