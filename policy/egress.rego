package platform.egress

import rego.v1

# Capabilities that run entirely on-premises — no remote inference, no egress risk.
local_capabilities := {"ocr", "anomaly"}

# ── allow ──────────────────────────────────────────────────────────────────────

default allow := false

# Local capabilities never leave the platform; always permitted.
allow if {
	input.capability in local_capabilities
}

# Remote capabilities are allowed only when classification is non-sensitive
# AND the tenant has not restricted residency to on-premises.
allow if {
	not (input.capability in local_capabilities)
	not (input.classification in {"confidential", "restricted"})
	input.residency != "on_prem_only"
}

# ── deny_reason ────────────────────────────────────────────────────────────────
# Empty string when allowed; first matching denial reason when denied.

default deny_reason := ""

deny_reason := "classification:confidential — data may not egress to a remote provider" if {
	not allow
	input.classification == "confidential"
	not (input.capability in local_capabilities)
}

deny_reason := "classification:restricted — data may not egress to a remote provider" if {
	not allow
	input.classification == "restricted"
	input.classification != "confidential"
	not (input.capability in local_capabilities)
}

deny_reason := "residency:on_prem_only — remote egress is prohibited for this request" if {
	not allow
	input.residency == "on_prem_only"
	not (input.classification in {"confidential", "restricted"})
	not (input.capability in local_capabilities)
}
