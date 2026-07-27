# BTC Trend-Flex Checklist

## Before trade

- `BTC only`
- if using M15 executor: bias from `H1`, execution `M15`, trigger `M5`
- `H4` context from BitMasterAI checked before any intraday trigger
- `D1/W1` levels mapped
- active scenario written in one line: `bull / base / bear`
- default risk fixed at `1.0%`
- if data degraded: risk reduced to `0.5%`
- today not more than `2R` down
- week not more than `6R` down

## Regime

- `ADX >= 25` -> trend mode only
- `ADX < 18` -> range mode only
- `ADX 18..24` -> no trade

## Market inputs

- `OI`: confirm, squeeze warning, crowding, or capitulation?
- funding: balanced, overheated longs, or crowded shorts?
- liquidation heatmap: where is nearest cluster?
- spot vs perp: spot confirms or derivatives are driving noise?
- volume / delta: real impulse or weak follow-through?
- ETF / institutional flows: background tailwind or headwind?
- HTF context: accumulation, impulse, distribution, or balance?

## Scenario gate

- `bull`: support holds, OI supports, spot confirms
- `base`: range edges respected, follow-through weak
- `bear`: rallies fail, upside sweep rejects, spot weak

Rule:

- if no scenario is clearly dominant, reduce aggression
- if scenario changes mid-session, re-evaluate before any new entry

## Decision math

- `win rate` is a system statistic, not an entry trigger
- this trade needs a real structural stop and a real structural target before entry
- `trend continuation` requires planned `RR >= 1.5R` to final target
- `range reversion` requires planned `RR >= 1.2R` to final target
- if fees, slippage or nearest HTF liquidity destroy the edge -> skip
- think in `R`, not in money
- if recent rolling expectancy is degrading, trade smaller or stop

## Trend long

- price above `EMA200`
- `DI+ > DI-`
- pullback into `EMA20/50`
- `PSAR` flip up
- bullish `MACD` cross
- close back above `EMA20`
- `OI` should support the move
- funding should not be extremely crowded against continuation
- spot or volume / delta should confirm
- no major liquidation cluster directly above entry

Entry:

- buy stop above signal candle high

Stop:

- below pullback low - `0.3 ATR`

Manage:

- `TP1 1.2R` -> partial + BE
- `TP2 2.0R` or trail by `PSAR`

## Range long

- confirmed range with `2x2` touches
- price at support / lower BB
- `RSI < 30`
- `Stoch K < 20` and crosses up
- bullish rejection close
- liquidity sweep + reclaim improves quality
- `OI` flush or extreme funding helps reversal case
- volume response at the edge is preferred

Entry:

- buy stop above signal candle high

Stop:

- below range low - `0.25 ATR`

Manage:

- partial at midrange
- final near `90%` of opposite edge
- no movement for `6` execution candles -> exit

## Shorts

- mirror the long rules

## Fast pre-entry gate

Ask:

- where is nearest liquidity?
- is `OI` confirming or fading the move?
- who pays funding?
- does spot / volume confirm?
- is this range edge or middle?
- is macro / ETF background strongly against the trade?

Rule:

- `3+` dirty answers -> skip

## Hard no-trade

- middle of range
- against `EMA200` in trend mode
- `ADX` neutral zone
- after `2R` daily stop is hit
- if stop distance > `1.5 ATR` in trend setup
- if scenario is not confirmed by at least `3` key dimensions
- if derivatives data is missing and the setup is not clean enough to survive as pure price action
- directly into a major liquidity cluster
- if the target exists only to make the `RR` look pretty
- after a 3-loss streak without reset / review
- if you are trying to recover previous losses with larger size

## Truth check

- `70%+` is a target, not a promise
- judge system only after `100+` logged trades with unchanged rules
- if derivatives / spot context is unavailable, reduce size and tag the trade as `data_degraded`
- `expectancy > 0` matters more than a beautiful isolated win rate
