# H02: BTC to ETH short-horizon lead-lag

## Mechanism

A large completed BTC 15-minute move may lead ETH when ETH has moved in the
same direction but by no more than 40 percent of the BTC move. The test follows
the BTC direction in ETH on the next bar.

## Frozen contract

- Instruments: `BTCUSDT`, `ETHUSDT`
- Venue/data class: Binance Spot public monthly data
- Endpoints:
  - `spot/monthly/klines/BTCUSDT/15m`
  - `spot/monthly/klines/ETHUSDT/15m`
- Resolution: 15 minutes
- BTC event: absolute close-to-close return at least `0.008`
- ETH alignment: same sign or zero
- ETH lag: absolute ETH return at most `0.40 * abs(BTC return)`
- Entry: ETH next 15-minute open after the completed event bar
- Exit: ETH open four 15-minute bars after entry
- Overlap: prohibited
- Round-trip fee: 10 bps entry plus 10 bps exit
- Round-trip slippage: 2 bps entry plus 2 bps exit
- Total cost deducted once: 24 bps
- Extension: frozen monthly files for January through June 2026
- Prior: accepted R57 2025 OOS trade ledger, imported unchanged

## Disposition gates on cumulative 2025 plus 2026 OOS

- Fewer than 40 trades: `INSUFFICIENT_DATA`
- At least 100 trades, positive mean, positive median, positive 95 percent
  bootstrap lower mean, and positive-return quarter concentration at most
  70 percent: `KEEP_FOR_FORWARD_WATCH`
- Otherwise: `KILL`

No threshold, cost, horizon, venue, or instrument adaptation is permitted.

`can_trade=false`

`capital_permission=DENY`
