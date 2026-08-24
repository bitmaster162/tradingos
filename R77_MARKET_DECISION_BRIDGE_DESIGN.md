# TradingOS R77 — Market Decision Bridge R1

## Why this slice

The current canonical pipeline already has:
1. Watchtower — multi-timeframe market-state facts and directional watch bias.
2. Liquidity Lens — visible-book microstructure context.
3. Market Radar — ranked multi-asset attention plus liquidity friction/veto context.
4. Decision Brief v2 — deterministic single-symbol evidence/edge gate.
5. Decision Cockpit/operator surfaces.

The missing link is not another scorer. It is a deterministic, replayable adapter from
the exact upstream market evidence into the Decision Brief snapshot contract.

## R77 contract

Inputs:
- raw `tradingos.binance_watchtower_capture.v1`
- `tradingos.watchtower.v1`
- `tradingos.market_radar.v1`

Output:
- nested Decision Brief-compatible BTCUSDT/4h snapshot
- exact SHA-256 of that snapshot
- input-binding hashes
- Radar attention context kept outside the snapshot and marked `confers_authority=false`
- deny-only safety ceiling

## Freshness rule

R77 must NOT assign `captured_at` to every source.

Source `observed_at` is taken from raw evidence:
- OHLCV: latest closed BTCUSDT futures 4h kline close time
- open interest: `open_interest.time`
- funding: `mark_price.time` (the source of current funding/basis facts)
- spot flow: latest closed BTCUSDT spot 4h kline close time

Every source timestamp must be at or before capture time.

## Authority rule

Radar bias is attention context only in the R77 output. It is not copied into the
Decision Brief snapshot as a stance. Decision Brief v2 independently computes its
own evidence and watch stance.

Hard ceiling:
- execution_authority = NONE
- signals_allowed = false
- orders_allowed = false
- can_trade = false
- capital_permission = DENY
- confers_authority = false

No network calls. No credentials. No AI-generated market facts.
