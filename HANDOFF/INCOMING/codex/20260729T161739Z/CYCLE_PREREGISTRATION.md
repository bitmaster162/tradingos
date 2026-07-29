# R57 bounded edge research cycle 02

Freeze time: `2026-07-29T16:17:39Z`

This cycle contains exactly three hypotheses. Parameters are fixed before any
2025 outcome data is downloaded or inspected.

## Shared protocol

- In-sample: `2024-01-01T00:00:00Z` through `2024-12-31T23:59:59Z`.
- Out-of-sample: `2025-01-01T00:00:00Z` through `2025-12-31T23:59:59Z`.
- Public source: monthly Binance Vision archives.
- Entry and exit use different, strictly time-ordered bar opens.
- No overlapping positions within a hypothesis.
- Costs are deducted once as explicit round-trip basis points.
- No parameter search, ranking, or replacement after OOS inspection.
- OOS is the only disposition authority. IS is diagnostic only.
- Bootstrap uses 10,000 deterministic resamples with seed `5702`.
- `KEEP_FOR_FORWARD_WATCH` is research status only, never trading permission.

The hypotheses are:

1. `H01_FUNDING_EXTREME_REVERSAL` — funding.
2. `H02_BTC_ETH_LEAD_LAG` — lead-lag.
3. `H03_BTC_COMPRESSION_BREAKOUT` — volatility breakout.

`can_trade=false`; `capital_permission=DENY`.
