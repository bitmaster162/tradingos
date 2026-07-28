# TradingOS VIP Daily Decision Brief

**BTCUSDT · 4h · 2026-07-29T00:00:00Z**

> **WATCH_LONG** — edge_gate_passed. This is a read-only operator brief, not a signal or order.

## Today

- Regime: `TREND_UP` / `NORMAL`
- Input status: `READY`
- Edge sufficient: `True`
- Score margin: `6.5`
- One next action: **Wait for a 4h close above 119600.0 with spot-flow and OI confirmation; do not place an order from this brief.**

## Competing Intent Hypotheses

### LONG

- Support score: `6.5`
- Counter score: `0`
- Independent support dimensions: `5`
- Supporting evidence:
  - HTF trend: trend=up (weight 2.0)
  - EMA alignment: ema_fast > ema_slow (weight 1.0)
  - Price/OI alignment: price=1.8% OI=2.1% (weight 1.25)
  - Spot CVD: spot=up, perp=up (weight 1.25)
  - Relative volume confirmation: relative_volume=1.35 (weight 1.0)
- Contradicting or ambiguous evidence:
  - none

### SHORT

- Support score: `0`
- Counter score: `6.5`
- Independent support dimensions: `0`
- Supporting evidence:
  - none
- Contradicting or ambiguous evidence:
  - HTF trend: trend=up (weight 2.0)
  - EMA alignment: ema_fast > ema_slow (weight 1.0)
  - Price/OI alignment: price=1.8% OI=2.1% (weight 1.25)
  - Spot CVD: spot=up, perp=up (weight 1.25)
  - Relative volume confirmation: relative_volume=1.35 (weight 1.0)

## Scenarios

### BULL

- Trigger: 4h close above 119600.0 with spot-flow and OI confirmation
- Invalidation: 4h close back below 119600.0 or loss of 116800.0
- Use: reassess WATCH_LONG; this brief itself is not an entry signal

### BASE

- Trigger: price remains between 116800.0 and 119600.0
- Invalidation: accepted close outside [116800.0, 119600.0]
- Use: NO_ACTION in the middle of the range; wait for new evidence

### BEAR

- Trigger: 4h close below 116800.0 with spot-flow and OI confirmation
- Invalidation: 4h close back above 116800.0 or reclaim of 119600.0
- Use: reassess WATCH_SHORT; this brief itself is not an entry signal

## Derivatives Context

- OI: `leverage_building` (2.1%)
- Funding: `balanced_or_unconfirmed` (z=0.7)
- Basis z: `0.6`
- Liquidation bias: `balanced`

## Uncertainty And Data Quality

- Snapshot age: `30.0` minutes
- Missing: `none`
- Conflicts: `none`
- Blockers: `none`
- Scores are not probabilities. `WATCH_*` is not permission to trade.

## Pilot Feedback

- Prior decision: `NO_ACTION`
- Changed decision: `WATCH_LONG only after confirmation`
- Prevented decision: `Prevented chasing price before a confirmed close`

## Provenance

- Brief ID: `e0992cf3e700197dc80bd76d`
- Input SHA-256: `7fde85171fd7e1f4906467159e15d41ffe4070b8a1063f7631b6869d93af942a`
- Policy: `TRADINGOS_DECISION_BRIEF_POLICY_V1`
- Generated: `2026-07-29T00:30:00Z`
- `can_trade=false`; capital permission `DENY`.
