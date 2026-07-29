# H03: BTC volatility-compression breakout

## Economic class

Directional volatility breakout.

## Frozen hypothesis

A close outside a narrow prior 24-hour range predicts continuation over the
next twelve hours.

## Universe and sources

- Binance Spot `BTCUSDT`.
- Monthly `1h` kline archives.

## Signal and execution

- At signal bar `t`, calculate the high and low of bars `t-24` through `t-1`.
- Compression requires `(prior_high - prior_low) / close[t-1] <= 0.03`.
- Long signal: `close[t] > prior_high`.
- Short signal: `close[t] < prior_low`.
- Entry is the next hourly bar open.
- Exit is the open twelve bars after entry.
- No overlapping positions.
- Round-trip cost: `24 bps`, deducted once:
  - entry fee `10 bps`;
  - exit fee `10 bps`;
  - entry slippage `2 bps`;
  - exit slippage `2 bps`.

## Invalidation and disposition

- Invalid event: missing hourly continuity or missing entry/exit.
- `INSUFFICIENT_DATA`: fewer than 25 OOS trades.
- `KEEP_FOR_FORWARD_WATCH`: at least 50 OOS trades, positive net mean,
  positive bootstrap 95% lower bound, profit factor above `1.10`, and no
  quarter supplies more than 70% of positive PnL.
- Otherwise `KILL`.
