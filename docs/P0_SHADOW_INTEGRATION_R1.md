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
SCT R13
  -> sct.prediction/v3 (arm=sct, execution_authority=NONE)
TradingOS thesis
  -> tradingos.trade_thesis.v1
TRIAXIS audit request compiler
  -> triaxis.trade_audit_request.v1
Independent TRIAXIS evidence-first audit
  -> triaxis.trade_adjudication.v1
Risk veto
  -> fail-closed shadow risk vector
TradingOS
  -> tradingos.trade_decision_packet.v1
Human reveal + later market outcome
  -> tradingos.trade_outcome_receipt.v1
```

## TRIAXIS request

The P0 compiler binds the exact `TradeCase`, exact thesis hash and exact market evidence refs into one independent audit request.

Current TRIAXIS evidence does **not** justify mandatory persona debate. The operational request therefore uses:

- strongest evidence-bound support for the thesis;
- direct falsification of hidden assumptions, stale evidence, regime mismatch, liquidity traps, contradictory flow, invalidation, sizing logic and operator-bias risk;
- countermodel default **OFF**, eligible only when direct evidence leaves competing explanations live;
- trialectic closure containing only surviving claims with uncertainty preserved;
- evidence audit binding every surviving material claim to supplied evidence or marking it unsupported.

The compiler performs no model call, tool call, order, signal or runtime effect. TRIAXIS remains a contestant/auditor, not an oracle.

## SCT R13 compatibility

P0 consumes the current SCT prediction contract:

`sct.prediction/v3`

A top-probability tie is **not** lexicographically resolved. In a tie:

```text
predicted_choice=None
prediction_status=TIE
divergence=None
divergence_status=UNDEFINED_TWIN_TIE
```

After human reveal, Twin fidelity for that case is `UNSCORABLE_TIE`, not a forced win/loss.

This preserves SCT R13 semantics and prevents TradingOS from inventing a human prediction that SCT itself did not make.

## Invariants

- `prediction != permission`.
- Every valid trade-action option set contains `WAIT`; fail-closed vetoes therefore always have a legal terminal action.
- Vision evidence never creates a signal or order permission.
- TRIAXIS request and adjudication are evidence/advice only and have `execution_authority=NONE`.
- Any risk veto forces `WAIT` in the shadow packet.
- `HOLD`, `REJECT`, or `REVISE` from TRIAXIS forces `WAIT` in the shadow packet.
- Human reveal must belong to the exact frozen case option set.
- Twin fidelity, human/advisor divergence and trade quality/PnL remain separate outcome dimensions.
- `TradeCase`, `TradeThesis`, TRIAXIS adjudication and `TradeDecisionPacket` are hash-bound at their consumption boundaries.
- Stale SCT v2 predictions fail closed rather than being silently upgraded.

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
- `tools/unified_shadow_federation.py`
- `tests/test_unified_shadow_federation.py`
- `docs/UNIFIED_SHADOW_FEDERATION_P0_R1.md`

Cross-repository coupling is by typed, hash-bound records so SCT, VisionAssist, TRIAXIS and all other federation nodes retain their own ownership and release lifecycle.
