# TradingOS R82 — Human Review Attestation Boundary R1

## Objective

R82 closes the deliberately manual end of canonical R81 with a deterministic,
offline attestation boundary over an already-valid R81 shadow report.

Pipeline:

`exact R81 shadow report → bounded human-supplied review input → R82 validation → immutable attestation → human/archive`

R82 does not make the shadow report more authoritative. It only records a bounded
review disposition against the exact report bytes and exact R82 review policy.

## Exact report binding

R82 accepts only an R81 report with the canonical R81 key set, count/integrity-only
mode, no-authority ceiling, valid deterministic `report_id`, and self-consistent counts.
The attestation binds:
- exact R81 `report_id`;
- SHA-256 of the complete canonical R81 report payload;
- exact R81 shadow-policy SHA-256 carried by the report;
- exact frozen-set declaration ID carried by the report;
- exact R82 review-policy SHA-256.

Any report mutation, substituted report ID, count drift, integrity drift, or safety
ceiling drift is rejected before review input is accepted.

## Bounded review input

R82 accepts exactly two human-supplied fields:
- `disposition`;
- `reason_codes`.

Allowed dispositions are:
- `ACKNOWLEDGED`;
- `DISPUTED`;
- `FOLLOWUP_REQUIRED`.

Reason codes are a small fixed vocabulary. Free text, recommendations, rankings,
probabilities, prices, returns, P&L, model/provider choices, policy changes, signals,
orders, or execution instructions are not accepted.

R82 deliberately does **not** authenticate reviewer identity. The attestation therefore
records `review_origin=UNVERIFIED_HUMAN_INPUT`; identity/attestation infrastructure is a
separate future problem and cannot be inferred from this artifact.

## Deterministic attestation

The `attestation_id` binds the complete attestation payload except the ID itself.
Reason codes are unique and canonical-order sorted. Any post-build mutation fails
validation.

## Authority ceiling

R82 remains offline evidence only:
- `shadow_only=true`
- `human_review_only=true`
- `review_origin=UNVERIFIED_HUMAN_INPUT`
- `report_consumption_authority=NONE`
- `memory_write_authority=NONE`
- `policy_update_allowed=false`
- `live_decision_feedback_allowed=false`
- `live_decision_use_allowed=false`
- `model_selection_use_allowed=false`
- `execution_authority=NONE`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`

No persistence, network/provider transport, credential access, process execution,
deployment, runtime registration, model selection, signals, orders, wallet, or capital
effect exists in R82 core.
