# TradingOS R80 — Retrospective Memory Threat Model R1

## Trust boundaries

1. Canonical deterministic Decision Brief.
2. Canonical R78 validated request/response.
3. Canonical R79 transport envelope/receipt.
4. Offline retrospective annotation.
5. R80 immutable record builder.
6. Count-only calibration summary.
7. Future R81 shadow evaluation.

## Threats and R1 controls

| Threat | R1 control |
| --- | --- |
| Cross-request replay | Request, brief, envelope, receipt, response hashes all bound |
| Outcome attached to wrong response | Exact response SHA + exact claim-id set required |
| Invented retrospective claim | Annotation claim IDs must equal validated response claim IDs |
| Selective claim omission | Exactly one outcome required for every response claim |
| Duplicate claim outcome | Claim IDs must be unique |
| P&L backdoor | P&L/return/price fields forbidden by exact schema/key sets |
| Probability backdoor | Outcome values categorical only; summary counts only |
| Self-training | weight/prompt/model/policy updates hard-denied |
| Live feedback | live decision feedback hard-denied |
| Memory becomes authority | memory_write_authority=NONE; confers_authority=false |
| Persistent side effects | core contains no DB/filesystem/network writer |
| External-source contamination | external sources denied in R80 policy |
| Record mutation | deterministic `record_id` recomputed over the full record payload except the ID itself |
| Duplicate record inflation | calibration summary rejects duplicate record IDs |
| Summary mutation | deterministic `summary_id` recomputed over the full summary payload except the ID itself |
| Mixed-policy aggregation | all records must bind the same exact R80 policy SHA |
| Trading optimization | P&L/trading-performance use denied |
| Model ranking via counts | model/provider ranking output absent from R1 summary |

## Deliberately deferred

R80 R1 does not provide persistent storage, retention policy, human identity/attestation,
economic outcome scoring, P&L attribution, model ranking, probability calibration,
online learning, or live feedback.

R81 may evaluate a frozen record set in shadow mode, but it must remain separately gated.
