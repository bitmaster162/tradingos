# R62 BTC crowding-exhaustion challenge

Work order: `CODEX02-R62-BTC-CROWDING-EXHAUSTION-CHALLENGE`

Freeze time: `2026-07-29T20:22:28Z`

## Single hypothesis

When BTCUSDT perpetual open interest is elevated and growing, funding and the
top-trader position ratio indicate long crowding, and short-horizon price
momentum fails inside a still-positive 24-hour context, subsequent BTC 1h and 4h
returns underperform regime-matched controls after one explicit round-trip cost.

No second hypothesis, parameter search, or outcome-driven universe replacement
is permitted.

## Instrument, venue, and source clocks

- Instrument: Binance USD-M perpetual `BTCUSDT`.
- Price: completed public 1h futures klines.
- OI: `sum_open_interest` from public Binance Vision metrics.
- Top-trader position ratio:
  `sum_toptrader_long_short_ratio` from the same metrics rows.
- Metrics clock: last metrics observation at or before each completed 1h close,
  with maximum age 10 minutes.
- Funding: `last_funding_rate`; last `calc_time` at or before the completed 1h
  close, never forward-filled beyond eight hours.
- Warm-up source period: 2025-06-01 through 2025-06-30.
- Calibration period: 2025-07-01 through 2025-12-31.
- Frozen OOS period: 2026-01-01 through the final complete June 2026 bar.
- Exact monthly public URLs are frozen in `FROZEN_SOURCE_PLAN.json`.

## Features

- `oi_level_z_30d`: current hourly OI versus the previous 720 hourly OI
  observations, excluding the current observation.
- `oi_change_4h`: current OI divided by OI four completed hours earlier minus 1.
- `funding_rate`: most recent causally available funding print.
- `top_position_ratio`: most recent causally available
  `sum_toptrader_long_short_ratio`.
- `ret_1h`, `ret_4h`, `ret_24h`: completed close-to-close returns.
- `rv_24h`: population standard deviation of the previous 24 completed hourly
  returns.

All quantile thresholds below are calculated once from calibration features
only and then frozen before OOS evaluation.

## Primary threshold band

- Elevated OI level: `oi_level_z_30d >= calibration q65`.
- Growing OI: `oi_change_4h >= max(0, calibration q55)`.
- Long funding: `funding_rate >= max(0, calibration q60)`.
- Top-trader crowding: `top_position_ratio >= calibration q65`.
- Momentum failure: `ret_24h > 0`, `ret_4h <= 0`, and `ret_1h <= 0`.

Signals whose 4h evaluation horizons overlap are de-duplicated chronologically.

## Neighbor sensitivity band

One predeclared looser neighboring band is reported without best-band
selection:

- OI level q60.
- OI 4h change `max(0, q50)`.
- Funding `max(0, q55)`.
- Top-trader position ratio q60.
- Identical momentum, timing, controls, costs, and disposition logic.

The sensitivity result may only confirm or weaken sign stability. It cannot
replace the primary sample.

## Causal entry and outcomes

- The signal is evaluated only after a 1h bar is complete.
- Entry is the open of the immediately following 1h bar.
- The independent +1h exit is the next 1h open after entry.
- The independent +4h exit is the fourth 1h open after entry.
- Entry and exit are always different price snapshots.
- No same-snapshot entry/exit is allowed.

## Cost ledger

Each horizon is evaluated as an independent hypothetical short:

- entry taker fee: 5 bps;
- exit taker fee: 5 bps;
- entry slippage: 1 bp;
- exit slippage: 1 bp;
- total round-trip cost deducted once: 12 bps.

## Regime-matched controls

For each signal, select exactly five nearest OOS non-signal candidate timestamps:

- from the same chronological half;
- with the same calibration-frozen `rv_24h` tercile;
- with the same sign of `ret_24h`;
- with the same integer funding-age hour bucket;
- outside plus or minus four hours of every signal timestamp;
- with complete +1h and +4h outcomes.

Matching uses features and timestamps only, never future outcomes. A signal
without five controls is excluded and reported.

Matched underperformance after costs is:

`mean(control_return) - signal_return - 0.0012`

## Chronological split and evidence

- Half 1: entry before `2026-04-01T00:00:00Z`.
- Half 2: entry at or after that timestamp.
- Paired bootstrap: 10,000 resamples of per-signal matched underperformance.
- Seed: `6202`.
- Report full sample and both halves separately for +1h and +4h.

## Disposition

- Fewer than 30 matched primary OOS signals: `INSUFFICIENT_DATA`.
- Source/feature OOS coverage below 95 percent: `INSUFFICIENT_DATA`.
- Otherwise `KEEP_FOR_LARGER_FORWARD_WATCH` only if:
  - full +4h mean matched underperformance is positive;
  - full +4h median is positive;
  - +4h bootstrap 95 percent lower mean is positive;
  - both chronological halves have positive +4h means;
  - full +1h mean matched underperformance is non-negative;
  - the neighboring sensitivity band has non-negative +4h mean.
- Any other classified result: `KILL`.

## Prohibited adaptations

- No threshold, quantile, feature, source, cost, clock, horizon, control, split,
  bootstrap, or sample-floor changes after this freeze.
- No same-snapshot entry/exit.
- No backfill after evaluation.
- No outcome-driven symbol, venue, or time-window substitution.
- No TradingOS implementation changes.
- No signal, order, Scheduler, service, permission, or capital effect.

`can_trade=false`

`capital_permission=DENY`
