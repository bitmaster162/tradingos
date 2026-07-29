# H02: BTC leads a lagging ETH response

## Economic class

Cross-asset lead-lag.

## Frozen hypothesis

A large 15-minute BTC move that ETH has not yet matched predicts ETH movement
in the same direction over the next hour.

## Universe and sources

- Binance Spot `BTCUSDT` and `ETHUSDT`.
- Monthly `15m` kline archives.
- Bars are joined only by identical open timestamps.

## Signal and execution

- BTC close-to-close return magnitude is at least `0.008`.
- BTC and ETH returns have the same sign or ETH return is zero.
- `abs(ETH return) <= 0.40 * abs(BTC return)`.
- Direction is the BTC return sign.
- Entry is ETH open on the next 15-minute bar.
- Exit is ETH open four bars after entry.
- No overlapping positions.
- Round-trip cost: `24 bps`, deducted once:
  - entry fee `10 bps`;
  - exit fee `10 bps`;
  - entry slippage `2 bps`;
  - exit slippage `2 bps`.

## Invalidation and disposition

- Invalid event: either bar missing, timestamp mismatch, or entry/exit gap.
- `INSUFFICIENT_DATA`: fewer than 40 OOS trades.
- `KEEP_FOR_FORWARD_WATCH`: at least 100 OOS trades, positive net mean,
  positive bootstrap 95% lower bound, positive median, and no quarter supplies
  more than 70% of positive PnL.
- Otherwise `KILL`.
