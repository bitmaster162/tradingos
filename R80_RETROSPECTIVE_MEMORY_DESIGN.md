# TradingOS R80 — Retrospective Memory & Calibration R1

## Objective

R80 adds an **offline retrospective evidence-memory layer** after canonical R78/R79.
It records what a validated bounded analyst said and how those claims were later
classified by a bounded offline evaluator.

Pipeline:

`deterministic brief → R78 bounded analysis → R79 transport receipt → human/offline outcome annotation → R80 immutable retrospective record → count-only calibration summary`

R80 does not participate in the live decision path and cannot change any current or
future market decision by itself.

## Memory is not learning

R80 R1 deliberately separates:
- memory from authority;
- retrospective evidence from predictive inference;
- calibration counts from probabilities;
- postmortem records from self-modifying prompts, models, weights, or policies.

R80 does **not**:
- update model weights;
- update prompts;
- select a model/provider;
- change R78/R79 policy;
- change deterministic market facts;
- create signals/orders;
- record or optimize P&L;
- write to a persistent store in core;
- feed any summary automatically into a live decision.

## Retrospective record

Each record binds exact hashes for:
- R78 request;
- Decision Brief;
- R79 transport envelope;
- R79 transport receipt;
- validated R78 response;
- R80 memory policy;
- bounded outcome annotation.

The deterministic `record_id` binds the full record payload except the ID itself, so
any post-build mutation to chain digests, claim outcomes, or the safety ceiling fails validation.

Each response claim must have exactly one categorical outcome:
- `SUPPORTED`
- `CONTRADICTED`
- `UNRESOLVED`
- `NOT_EVALUABLE`

The annotation may only reference existing claim IDs from the validated response.
No new claims, market facts, prices, returns, P&L, or probabilities are accepted.

## Calibration summary

R80 R1 aggregates **integer counts only** by claim kind and outcome. A deterministic
`summary_id` binds the full summary payload except the ID itself so count-field mutation
fails validation.

It does not emit:
- rates;
- percentages;
- predictive probabilities;
- confidence scores;
- model rankings;
- automatic model/prompt/policy changes.

Those would require a separate R81 shadow-evaluation design and independent gates.

## Authority ceiling

- `memory_write_authority=NONE`
- `auto_learning_allowed=false`
- `live_decision_feedback_allowed=false`
- `execution_authority=NONE`
- `can_trade=false`
- `capital_permission=DENY`
- `confers_authority=false`
