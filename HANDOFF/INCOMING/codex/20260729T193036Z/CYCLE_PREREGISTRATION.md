# R59 bounded edge research cycle 03

Work order: `CODEX02-R59-BOUNDED-EDGE-RESEARCH-CYCLE-03`

Freeze time: `2026-07-29T19:30:36Z`

## Scope

Exactly two unresolved R57 watchlist hypotheses are continued:

1. `H01_FUNDING_EXTREME_REVERSAL`
2. `H02_BTC_ETH_LEAD_LAG`

Both are BTC-focused. H01 tests a derivatives funding mechanism. H02 tests a
cross-asset lead-lag mechanism. The killed R57 compression breakout is excluded.
No new hypothesis was selected after inspecting 2026 outcomes.

## Information-gain ranking

1. H02 ranked first because seven frozen 2025 OOS observations already exist and
   a six-month 15-minute extension has the highest chance of reaching its
   classification floor.
2. H01 ranked second because its 2025 OOS count was zero, but a new funding
   regime can directly falsify the claim or establish whether the event remains
   too rare to evaluate.

## Frozen observation window and sources

- Observation extension: `2026-01-01T00:00:00Z` through the last complete bar
  available in the frozen June 2026 monthly archives.
- Source: public read-only Binance Vision monthly archives.
- Exact URLs are enumerated in `FROZEN_SOURCE_PLAN.json`.
- Retrieval and SHA-256 hashing occur only after this preregistration is committed.
- Prior 2025 OOS trades are imported unchanged from the accepted R57 result and
  hashed as predecessor evidence.

## Shared evaluation rules

- Entry is always the next bar open strictly after a completed signal observation.
- Exit is a later bar open; same-snapshot entry and exit are prohibited.
- Positions within a hypothesis may not overlap.
- Costs are deducted exactly once per round trip.
- Source records are frozen and hashed before evaluation.
- The 2025 prior and 2026 extension are merged without duplicate trade identities.
- Bootstrap seed remains `5702`; bootstrap repetitions remain `10000`.
- Positive-return concentration may not exceed 70 percent in one UTC quarter.
- A missing statistic, duplicate, chronology violation, source mismatch, or
  ambiguous prior fails closed.
- Results authorize research disposition only. They cannot create a signal,
  order, permission change, Scheduler change, or capital effect.

## Prohibited adaptations

- No threshold, horizon, cost, venue, symbol, or sample-floor changes.
- No outcome-driven universe replacement.
- No robust-tag or proxy substitution.
- No backfill after a result is calculated.
- No parameter search or retest of a killed hypothesis.
- No TradingOS implementation or canonical Active modification.

## Allowed dispositions

Each hypothesis receives exactly one:

- `KEEP_FOR_FORWARD_WATCH`
- `KILL`
- `INSUFFICIENT_DATA`

The cycle stops after both dispositions.

`can_trade=false`

`capital_permission=DENY`
