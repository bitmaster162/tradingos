# CODEX02-R62 — BTC crowding-exhaustion edge challenge

**Owner:** CODEX-02  
**Task class:** `PREREGISTERED_RESEARCH_AND_FALSIFICATION_ONLY`

First publish the completed current CODEX-02 result through the Return Broker.
Do not reconstruct a missing historical READY.

## Single hypothesis

Test whether this predeclared mechanism has forward information:

> When BTC perpetual open interest and long crowding are elevated while price
> momentum fails, subsequent 1h/4h BTC return underperforms regime-matched controls
> after explicit costs.

## Preregistration before outcome inspection

Freeze:

- instrument: Binance `BTCUSDT` perpetual;
- completed 1h bars only;
- OI source and 4h OI change;
- funding source and exact funding clock;
- top-trader position ratio source;
- price-momentum failure definition;
- volatility/regime definition;
- thresholds derived only from a trailing pre-freeze window;
- next-completed-bar entry semantics;
- independent +1h and +4h evaluation;
- one line-item cost ledger, deducted once;
- matched-control construction;
- invalidation and kill rules;
- minimum 30 forward/OOS signals; fewer → `INSUFFICIENT_DATA`;
- prohibited adaptive changes.

No same-snapshot entry/exit, no parameter changes after freeze, no signal or order.

## Required robustness

- split OOS sample chronologically;
- report both halves separately;
- bootstrap or permutation evidence with exact method and seed;
- inspect sensitivity to one neighboring threshold band without selecting the best;
- report rejected result fully.

## Terminal disposition

Exactly one:

- `KEEP_FOR_LARGER_FORWARD_WATCH`
- `KILL`
- `INSUFFICIENT_DATA`

Self-publish strict ZIP/SHA/READY through the broker.

`can_trade=false`; `capital_permission=DENY`.
