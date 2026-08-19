# TradingOS P0 Shadow Integration R1

Status: CANDIDATE BRANCH ONLY / SHADOW / NO ACTION

## Baseline

This candidate branch was created from exact TradingOS baseline:

`48e800b0ecf25fdc315490a11c5e10342db5eb3c`

No merge, deploy, runtime registration, exchange call, signal, order, credential mutation, trading or capital effect is part of this scope.

## Cross-system flow

```text
VisionAssist market observation
  -> tradingos.visual_market_evidence.v1
TradingOS market snapshot
  -> tradingos.shadow_trade_case.v1
SCT
  -> sct.prediction/v2 (arm=sct, execution_authority=NONE)
TradingOS thesis
  -> tradingos.trade_thesis.v1
TRIAXIS audit request compiler
  -> triaxis.trade_audit_request.v1
Independent TRIAXIS audit
  -> triaxis.trade_adjudication.v1
Risk veto
  -> fail-closed shadow risk vector
TradingOS
  -> tradingos.trade_decision_packet.v1
Human reveal + later market outcome
  -> tradingos.trade_outcome_receipt.v1
```

## TRIAXIS request

The P0 compiler binds the exact `TradeCase`, exact thesis hash and exact market evidence refs into one independent audit request. The request carries the operational TRIAXIS method:

- ANGEL: strongest evidence-bound case for the thesis;
- DEVIL: attack hidden assumptions, stale evidence, regime mismatch, liquidity traps, contradictory flow, invalidation, sizing logic and operator-bias risk;
- TRIALECTIC: preserve only what survives both attacks;
- EVIDENCE AUDIT: bind every surviving material claim to supplied evidence or mark it unsupported.

The compiler performs no model call, tool call, order, signal or runtime effect.

## Invariants

- `prediction != permission`.
- Every valid trade-action option set contains `WAIT`; fail-closed vetoes therefore always have a legal terminal action.
- Vision evidence never creates a signal or order permission.
- TRIAXIS request and adjudication are evidence/advice only and have `execution_authority=NONE`.
- Any risk veto forces `WAIT` in the shadow packet.
- `HOLD`, `REJECT`, or `REVISE` from TRIAXIS forces `WAIT` in the shadow packet.
- Human reveal must belong to the exact frozen case option set.
- Twin fidelity and trade quality remain separate outcome dimensions.
- Every object carries a deterministic SHA-256 identity over canonical JSON.

## Fixed safety vector

```text
mode=SHADOW
execution_authority=NONE
can_trade=false
capital_permission=DENY
orders_allowed=false
signals_allowed=false
```

## P0 implementation surface

- `tools/tradingos_shadow_integration.py`
- `tests/test_tradingos_shadow_integration.py`

This slice deliberately does not import SCT or VisionAssist code directly. Cross-repository coupling is by typed, hash-bound records so each subsystem retains its own ownership and release lifecycle.
