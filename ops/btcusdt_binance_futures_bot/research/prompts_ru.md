# Research pack: BTCUSDT Binance USDⓈ-M

Ниже — один пакет промтов под futures-first research, но с проверкой spot-portability.

## 0) Базовый системный промт

```text
Ты — lead quant researcher и systems designer для Binance BTC bot.

Контекст:
- Primary venue: Binance USDⓈ-M Futures
- Primary symbol: BTCUSDT perpetual
- Secondary compatibility target: Binance Spot BTCUSDT
- Цель: найти устойчивую alpha-логику, которая сохраняет expectancy net of fees, slippage, funding и execution constraints
- Без look-ahead, без leakage, без использования признаков, недоступных в момент t
- Нужен output в двух слоях:
  1) alpha-core, не завязанный на venue
  2) execution/risk shell для USDⓈ-M perp
- Отдельно отмечай, что переносится на Spot, а что нет

Формат ответа:
1. Hypothesis
2. Market regime
3. Required data
4. Features
5. Entry / Exit logic
6. Position sizing
7. Venue-specific execution logic
8. Backtest protocol
9. Failure modes
10. Kill criteria
11. Which parts transfer to Spot
```

## 1) Regime map + alpha-core

```text
Используя системный промт, построй regime map для BTCUSDT perpetual на Binance.

Нужно:
- определить regimes: trend, range, volatility compression, expansion, liquidation-like impulse
- разделить признаки на:
  a) venue-agnostic
  b) futures-native (funding, mark/index divergence, open interest, crowding ratios)
- предложить 2 baseline alpha ideas:
  1) trend continuation
  2) mean reversion only in range regime
- показать, какие части alpha-core можно потом перенести на Spot без потери смысла
- отдельно перечислить признаки, которые почти наверняка unstable или lead to overfitting
```

## 2) Futures execution engine

```text
Используя системный промт, спроектируй execution policy для BTCUSDT USDⓈ-M perpetual.

Требую:
- entry policy: passive-first vs taker fallback vs skip
- exit policy: reduce-only exits, emergency flatten, time stop
- distinction between normal orders and algo/conditional exits
- handling of partial fills
- handling of post-only behavior
- handling of stale signals
- policy under fast market / spike conditions
- which execution rules are specific to perpetual futures and do not exist on Spot
```

## 3) Audit бэктеста под perpetuals

```text
Сделай red-team аудит бэктеста для BTCUSDT perpetual strategy.

Проверь отдельно:
- look-ahead через candle close
- leakage через normalization/windowing
- неправильный учет mark price vs last price
- неправильный учет funding
- отсутствие liquidation / leverage bracket awareness
- неправильный учет symbol filters
- неправильный учет partial fills
- неверную симуляцию algo stops / trigger logic
- multiple testing / parameter mining

Выход:
- список типовых ошибок
- тест на каждую ошибку
- минимальный стандарт качества бэктеста
```

## 4) Risk engine под USDⓈ-M

```text
Спроектируй risk engine для BTCUSDT USDⓈ-M perpetual bot.

Нужно:
- hard cap on leverage
- bracket-aware sizing
- isolated margin policy
- max position notional
- funding-aware trade filter
- max daily loss
- cooldown after losses
- liquidation-distance guardrail
- stale-data kill-switch
- reject-spike kill-switch
- exchange-state divergence kill-switch
- emergency reduce-only flatten logic

В ответе раздели:
1) hard limits
2) soft limits
3) degradation policy when live metrics worsen
```

## 5) Portability review to Spot

```text
Возьми уже спроектированную BTCUSDT perpetual strategy и выполни portability review to Spot.

Нужно:
- удалить futures-only элементы (funding, leverage, liquidation logic, mark-price-specific logic, bracket logic)
- показать, остается ли signal edge после удаления этих элементов
- какие execution assumptions ломаются на Spot
- какие risk assumptions упрощаются на Spot
- итог: strategy is
  a) perp-only
  b) portable with degradation
  c) portable with minimal change
```

## 6) Live monitoring и drift

```text
Спроектируй monitoring framework для live Binance BTC bot, где primary venue — USDⓈ-M perp.

Нужны панели:
- gross pnl / net pnl / funding / fees
- realized slippage
- maker ratio
- fill ratio
- cancel-to-fill ratio
- reject rate
- algo trigger quality
- distance between expected and realized execution
- regime drift
- alpha decay
- risk limit utilization

Также:
- stop-trading criteria
- reduce-size criteria
- rules for switching to observation-only mode
```
