# CODEX-02 M2B Forward Edge Evidence

Task: `TRADING_EDGE_FORWARD_EVIDENCE_M2B`

This is a disposable, research-only proposal. It does not modify TradingOS Active,
register a strategy, emit a signal, place an order, or grant paper/live permission.

## Decision matrix

| Track | Terminal | Bounded evidence |
|---|---|---|
| `RANGE_REFINED_FORWARD` | `KILL` | The attractive full-history observer was later rejected by a selection-frozen untouched calendar OOS: 29 trades, `-0.240632R`; +10 bps stress `-0.411049R`. The family is tombstoned `no_retune`. |
| `HYP-SPOT-LEAD-001` | `INSUFFICIENT_DATA` | Strictly later local data produced 2 new resolved trades. The old 43 were not reused as new evidence; combined count is 45/80. Fresh base expectancy is `+0.022861R`, but +10 bps stress is `-0.189287R` on only two trades. |
| `LIQUIDATION_CONTINUOUS_SCORE` | `INSUFFICIENT_DATA` | Frozen bins are unchanged. One post-lock signal resolved in `low` with `-1R`; total is 1/30, inactive baseline 0/8, eligible non-inactive bin 0. |

No track received `KEEP_FOR_FORWARD_PAPER`, so no CODEX-05 handoff was created.

## Reproduction

The evaluator is stdlib-only and imports the exact M2A Git baseline's research
modules. The raw source snapshot is intentionally not embedded in this proposal;
the strict return carries file-level hashes, time cutoffs and source receipts.

```powershell
python tools/evaluate_m2b_forward_evidence.py `
  --raw-root D:\codex02_m2b_20260801T154214Z\evidence\raw `
  --repo-root D:\codex02_m2b_20260801T154214Z\repo `
  --out-dir D:\codex02_m2b_20260801T154214Z\evidence\replay
```

## Tests

- M2B targeted contract tests: `6/6 PASS`.
- Exact M2A predecessor suite: `64/64 PASS`.
- Relevant RANGE/liquidation root tests: `22/22 PASS`.
- Spot-led pytest baseline: `5/5 PASS`.
- Broad root unittest discovery: `110/112` passed; two pre-existing Bitunix
  import errors reference absent docs and are outside this proposal. No source
  file outside this handoff directory was changed.

## Boundaries

- `can_trade=false`
- `capital_permission=DENY`
- `deploy_permission=DENY`
- `self_application=false`
- `NO_FURTHER_AGENT_WORK=true`

