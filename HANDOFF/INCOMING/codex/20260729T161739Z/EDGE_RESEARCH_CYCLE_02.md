# R57 bounded edge-research cycle 02

## Terminal research result

No hypothesis qualified for forward-watch promotion.

| Hypothesis | OOS trades | OOS net mean | Bootstrap 95% lower | Decision |
|---|---:|---:|---:|---|
| BTC funding-extreme reversal | 0 | n/a | n/a | `INSUFFICIENT_DATA` |
| BTC to ETH lead-lag | 7 | +0.49699% | -1.83507% | `INSUFFICIENT_DATA` |
| BTC compression breakout | 212 | -0.09814% | -0.30730% | `KILL` |

## Findings

### H01 funding-extreme reversal

The frozen `abs(fundingRate) >= 0.0003` event threshold produced 44 IS trades
but no OOS trades in 2025. IS itself was negative after the frozen 12 bps
round-trip cost: mean `-0.13155%`, profit factor `0.8194`. The hypothesis is
not retuned. It needs 20 additional qualifying OOS events merely to classify
and 30 to reach the preregistered keep sample floor.

### H02 BTC to ETH lead-lag

Only seven OOS trades qualified. Their positive mean was driven by a small
sample: median `-0.52792%`, win rate `28.57%`, bootstrap lower bound
`-1.83507%`, and all positive PnL fell in one quarter. It needs 33 additional
frozen OOS trades to classify and 93 to reach the keep sample floor.

### H03 BTC compression breakout

This hypothesis is falsified under the frozen rules. OOS produced 212 trades,
net mean `-0.09814%`, net sum `-20.8062%`, win rate `42.92%`, profit factor
`0.8395`, and a negative bootstrap lower bound. IS was also negative. No more
observation is required; the hypothesis is closed for this cycle.

## Data and execution controls

- 120 immutable public Binance Vision monthly ZIPs, 2024 IS and 2025 OOS.
- 9,591,606 source bytes; every file has a unique SHA-256 in the source manifest.
- Entry and exit use strictly different bar opens.
- Overlapping positions are forbidden.
- Costs are line-item frozen and deducted exactly once.
- No parameter search, route replacement, private source, credential, order,
  position, or capital effect.

## Handoff

Accepted specifications for CODEX-05: **none**.

`can_trade=false`; `capital_permission=DENY`; `NO_FURTHER_AGENT_WORK=true`.
