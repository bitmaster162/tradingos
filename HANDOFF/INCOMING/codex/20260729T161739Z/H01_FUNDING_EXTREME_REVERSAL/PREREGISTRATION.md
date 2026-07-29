# H01: BTC funding-extreme reversal

## Economic class

Funding / contrarian reversion.

## Frozen hypothesis

When the absolute BTCUSDT perpetual funding rate is at least `0.0003` per
funding event, crowded positioning mean-reverts over the next eight hours.
Positive funding predicts a short return; negative funding predicts a long
return.

## Universe and sources

- Instrument: Binance USD-M `BTCUSDT`.
- Events: monthly `fundingRate` archives.
- Prices: monthly USD-M `BTCUSDT` `1h` klines.

## Signal and execution

- Event exists when `abs(fundingRate) >= 0.0003`.
- Direction is `-sign(fundingRate)`.
- Entry is the first hourly bar open strictly after `fundingTime`.
- Exit is the bar open exactly eight hours after entry.
- Signals occurring while a position is open are ignored.
- Round-trip cost: `12 bps`, deducted once:
  - entry taker fee `5 bps`;
  - exit taker fee `5 bps`;
  - entry slippage `1 bps`;
  - exit slippage `1 bps`.

## Invalidation and disposition

- Invalid event: missing/non-monotonic bars or missing entry/exit open.
- `INSUFFICIENT_DATA`: OOS has fewer than 20 non-overlapping trades.
- `KEEP_FOR_FORWARD_WATCH`: OOS has at least 30 trades, positive net mean,
  positive deterministic bootstrap 95% lower bound, and no single calendar
  quarter contributes more than 70% of positive PnL.
- Otherwise `KILL`.

No leverage, liquidation, order, or account assumption is made.
