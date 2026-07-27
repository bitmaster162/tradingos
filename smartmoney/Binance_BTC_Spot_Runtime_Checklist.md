# Binance BTC Spot Runtime Checklist

Статус: `research-to-runtime gate`

## 1. Scope gate

- venue: `Binance Spot`
- symbol: `BTCUSDT`
- mode: `Testnet / paper first`
- no leverage
- no futures

## 2. Data gate

- `klines` collected and persisted
- `aggTrades` collected and persisted
- account/user-data events available
- timestamps normalized
- stale-data detection present

Если любой пункт грязный — не запускать executor.

## 3. Exchange gate

- symbol filters validated before order submit
- signed request flow implemented
- recvWindow/timestamp discipline defined
- local state reconciles against exchange state
- reject handling exists

Если order validation ещё не жёсткая — только research/paper.

## 4. Strategy gate

- regime defined operationally
- signal available at time `t` without leakage
- baseline simpler than ensemble
- entry/exit/invalidation explicit
- fees/slippage included in evaluation

Если стратегия объяснима только “на словах” — не годится.

## 5. Backtest gate

- walk-forward done
- out-of-sample done
- regime split done
- partial fills modeled
- latency modeled
- exchange filters included
- red-team audit completed

Если нет хотя бы одного из этих пунктов — live запрещён.

## 6. Execution gate

- maker-first policy defined
- missed-trade cost considered
- partial fills handled
- cancel/fill accounting works
- kill-switches wired

## 7. Risk gate

- max position set
- max daily loss set
- cooldown after losses set
- stale-data kill-switch set
- reject-spike kill-switch set
- local vs exchange divergence kill-switch set

## 8. Monitoring gate

- gross vs net pnl
- fees
- slippage
- fill ratio
- cancel-to-fill ratio
- reject rate
- uptime
- latency
- live vs backtest drift
- regime drift

## 9. Live gate

Live можно разрешать только если:

- paper/runtime path стабилен
- monitoring живой
- risk hard-limits протестированы
- evidence pack обновлён

Если нет — остаёмся в `research / paper`.
