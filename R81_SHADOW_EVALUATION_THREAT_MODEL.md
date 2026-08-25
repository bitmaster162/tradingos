# TradingOS R81 — Frozen-Record Shadow Evaluation Threat Model R1

## Trust boundaries

1. Canonical R80 memory policy.
2. Canonical R80 retrospective records.
3. R81 frozen-set declaration.
4. R81 deterministic shadow evaluator.
5. R81 count-and-integrity-only report.
6. Human review.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Selective subset evaluation | declaration and supplied records must match exactly |
| Undeclared record injection | exact record-id and record-SHA binding |
| Record payload substitution | full canonical record SHA-256 bound in frozen set |
| Duplicate inflation | duplicate `record_id` rejected before evaluation |
| Mixed R80 policy | every record validated against the one exact R80 policy SHA |
| Invalid R80 input | canonical R80 record validator runs on every record |
| Input-order nondeterminism | bindings normalized by `record_id` |
| Frozen-set mutation | deterministic declaration ID and records digest |
| Report mutation | deterministic `report_id` over complete report payload |
| Probability/rate/confidence backdoor | no such output keys; exact report key set |
| P&L/price/return backdoor | no economic fields; exact report key set |
| Model/provider ranking | ranking output forbidden by policy and absent from report |
| Self-training | weight/prompt/model/policy updates hard-denied |
| Live feedback/use | live decision feedback/use hard-denied |
| Memory becomes authority | `memory_write_authority=NONE`; `confers_authority=false` |
| Persistence/network side effect | R81 core has no filesystem/database/network/process/provider client |
| Execution escalation | exact NONE / DENY / false ceiling on declaration and report |

## Deliberately deferred

R81 R1 does not provide statistical calibration, rates, confidence intervals, economic scoring, P&L attribution, model comparison/ranking, provider comparison/ranking, online learning, live feedback, persistent storage, retention policy, human identity/attestation, or runtime activation.

Any later capability that consumes R81 outputs for model selection, policy adaptation, live decisions, trading, or capital requires a separately designed and separately authorized stage.
