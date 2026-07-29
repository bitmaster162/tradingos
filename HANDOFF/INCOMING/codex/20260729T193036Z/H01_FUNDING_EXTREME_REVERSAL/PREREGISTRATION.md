# H01: BTC funding-extreme reversal

## Mechanism

An extreme BTCUSDT perpetual funding print may identify crowded directional
leverage. The test takes the opposite direction after the funding observation
and holds for eight hours.

## Frozen contract

- Instrument: `BTCUSDT`
- Venue/data class: Binance USD-M futures public monthly data
- Funding endpoint: `futures/um/monthly/fundingRate/BTCUSDT`
- Price endpoint: `futures/um/monthly/klines/BTCUSDT/1h`
- Resolution: one hour
- Event: `abs(last_funding_rate) >= 0.0003`
- Direction: short after positive funding, long after negative funding
- Entry: first 1h open strictly after `calc_time`
- Exit: open exactly eight 1h bars after entry
- Overlap: prohibited
- Round-trip fee: 5 bps entry plus 5 bps exit
- Round-trip slippage: 1 bp entry plus 1 bp exit
- Total cost deducted once: 12 bps
- Extension: frozen monthly files for January through June 2026
- Prior: accepted R57 2025 OOS trade ledger, imported unchanged

## Disposition gates on cumulative 2025 plus 2026 OOS

- Fewer than 20 trades: `INSUFFICIENT_DATA`
- At least 30 trades, positive mean, positive 95 percent bootstrap lower mean,
  and positive-return quarter concentration at most 70 percent:
  `KEEP_FOR_FORWARD_WATCH`
- Otherwise: `KILL`

No threshold, cost, horizon, venue, or symbol adaptation is permitted.

`can_trade=false`

`capital_permission=DENY`
