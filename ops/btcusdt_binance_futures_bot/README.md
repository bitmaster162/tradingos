# BTCUSDT Binance USDⓈ-M Bot Skeleton

Futures-first каркас для `BTCUSDT` на Binance USDⓈ-M. Он не пытается сразу быть “альфой”.
Его задача — дать правильный контур:

- normal orders и algo orders разделены;
- state хранится отдельно для `ORDER_TRADE_UPDATE`, `ALGO_UPDATE`, `ACCOUNT_UPDATE`;
- есть market collector для `aggTrade`, `markPrice@1s`, `kline`, `!contractInfo`;
- есть private consumer для `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `ALGO_UPDATE`, `ACCOUNT_CONFIG_UPDATE`, `listenKeyExpired`;
- есть bootstrap reconciliation из `openOrders`, `openAlgoOrders`, `account v3`, `positionRisk v3`;
- есть reconcile daemon, который периодически сверяет runtime-state с подписанным REST snapshot;
- есть targeted reconcile healer, который лечит order/account divergence без обязательного full replace;
- есть targeted order/algo query fallback для точечной проверки статуса по `clientOrderId` / `clientAlgoId`;
- есть countdown heartbeat daemon для `countdownCancelAll`;
- есть live breakout orchestration loop на `markPrice@1s` + `aggTrade` с dry-run по умолчанию;
- сырой websocket-поток архивируется в JSONL;
- есть parity breakout backtester поверх `markPrice@1s` + `aggTrade` + `bookTicker` + `localDepth` + `!contractInfo` + crowding JSONL;
- есть отдельный `BookTickerCollector` для top-of-book execution gating и более реалистичного replay;
- есть `DepthBookCollector`, который держит local order book через diff depth stream + REST snapshot sync;
- есть crowding collector для `openInterest`, `openInterestHist`, long/short ratios и taker buy/sell volume;
- crowding-aware score, adaptive sizing/abstention и volatility-targeted sizing можно включать и в live loop, и в parity backtest;
- есть depth-aware gate, queue-admission gate, exit-depth liquidity gate и passive fill model на top-N local depth, чтобы сократить разрыв между replay и live execution;
- есть multi-level depth sweep для более реалистичного taker exit pricing и execution-quality diagnostics в backtest output;
- есть synthetic tail replay beyond displayed top-N depth и live execution-quality report, чтобы видеть качество исполнения не только офлайн, но и в live loop;
- backtest теперь пишет baseline в `backtest/latest_report.json` и `backtest/latest_execution_quality.json`;
- есть `walkforward-breakout`, который гоняет rolling/anchored walk-forward по сетке breakout-параметров и пишет `backtest/latest_walkforward_report.json`;
- есть execution-drift daemon, который сравнивает live execution quality с backtest baseline и пишет guard-state в `live/guards/latest_execution_drift.json`;
- есть intraday protection daemon, который читает `apiTradingStatus` + `adlQuantile` и пишет guard-state в `live/guards/latest_intraday_protection.json`;
- есть pnl protection daemon, который читает runtime-state/anchor и пишет guard-state в `live/guards/latest_pnl_protection.json`;
- есть economics regime daemon, который собирает multi-day economics dashboard из authoritative session-truth reports и пишет `live/guards/latest_economics_regime.json`;
- есть direct economics sizing feedback: live breakout loop может читать `latest_economics_dashboard.json`, плавно сжимать размер входа даже до срабатывания hard-guard и, при отсутствии combined-guard, напрямую уважать `latest_economics_regime.json`;
- есть combined protection daemon, который сводит execution/intraday/pnl/session-truth/economics guards в один stateful guard с cooldown/hysteresis и пишет `live/guards/latest_combined_protection.json`;
- есть trade reconciliation daemon, который сверяет локальные fill-records c `/fapi/v1/userTrades` и `/fapi/v1/income`, затем пишет `live/guards/latest_trade_reconciliation.json`;
- есть session truth daemon, который строит authoritative economics summary по `userTrades + income + archive`, затем пишет `live/guards/latest_session_truth.json` и `live/reports/latest_session_truth.json`;
- live breakout loop умеет читать execution-drift guard, intraday protection guard, pnl protection guard, trade reconciliation guard, session truth guard и combined protection guard, затем переводиться в `reduce_size` / `observe_only`;
- есть daily report aggregator для `reports/YYYY-MM-DD/*`, включая pnl/intraday/execution/combined/trade-reconciliation/session-truth guard-слои;
- bootstrap/reconcile теперь несут synthetic `contractInfo`, чтобы лечить drift по `cs`/`bks`;
- есть payload builders и safe CLI для render/send/cancel normal + algo orders.

## Что уже заложено

- `BinanceRESTClient` для базовых REST вызовов.
- HMAC-подпись signed endpoints.
- URL builder для routed websocket paths.
- `StateStore` с отдельными реестрами normal/algo orders и bootstrap snapshot support.
- `ExecutionValidator` для `PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `MIN_NOTIONAL`, `PERCENT_PRICE`.
- `ExecutionPlanner` с maker-first входом и отдельными algo exits.
- `ExecutionGateway` для safe render/send/cancel flows и обработки `503 Unknown error` как execution-unknown state.
- `BootstrapSynchronizer` для signed runtime rehydration.
- `MarketCollector` и `PrivateStreamConsumer`.
- `BreakoutBacktester`, `ParityBreakoutBacktester` и `walkforward-breakout` для офлайн replay и rolling/anchored parameter selection.
- `ExecutionDriftDaemon`, `IntradayProtectionDaemon`, `PnLProtectionDaemon`, `TradeReconciliationDaemon`, `SessionTruthDaemon`, `EconomicsRegimeDaemon`, `CombinedProtectionDaemon` и `aggregate_daily_reports` для monitoring / degradation policy.
- `DepthBookCollector` и `LocalDepthBook` для diff-depth sync с `GET /fapi/v1/depth`.
- trade-flow gating по `aggTrade`, funding-aware filters, optional crowding gate и depth-imbalance gate для live loop.
- research pack на русском.

## Что это не делает

- не гарантирует прибыль;
- не содержит готовую alpha-модель;
- не реализует full local order book simulator с multi-level queue priority beyond top-N depth;
- не моделирует hidden liquidity и exchange-specific queue priority детерминированно;
- не заменяет live reconciliation через user streams;
- не включает portfolio-level orchestration по нескольким символам.

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
```

Показать routed market manifest без подключения:

```bash
python -m btcusdt_bot market-manifest
```

Проверить, хватает ли локальных JSONL для `mark_only` или полноценного `multistream_parity` backtest:

```bash
python -m btcusdt_bot backtest-readiness   --start-date 2026-04-01   --end-date 2026-04-07
```

Собрать пример entry + exit plan:

```bash
python -m btcusdt_bot plan-example --side BUY --mark-price 65000 --qty 0.001 --atr 450
```

Сделать signed bootstrap sync в локальный snapshot:

```bash
python -m btcusdt_bot bootstrap-sync
```

Короткий smoke-test market collector на 5 сообщений:

```bash
python -m btcusdt_bot collect-market --max-messages 5
```

Короткий smoke-test top-of-book collector на 5 сообщений:

```bash
python -m btcusdt_bot collect-book-ticker --max-messages 5
```

Короткий smoke-test diff-depth collector на 5 сообщений:

```bash
python -m btcusdt_bot collect-depth-book --max-messages 5 --depth-levels 20 --snapshot-limit 1000
```

Короткий smoke-test RPI diff-depth collector на 5 сообщений:

```bash
python -m btcusdt_bot collect-rpi-depth-book --max-messages 5 --depth-levels 20 --snapshot-limit 1000
```

Короткий smoke-test private consumer на 5 сообщений:

```bash
python -m btcusdt_bot consume-private --max-messages 5
```

Точечно запросить статус normal order и гидратировать local state:

```bash
python -m btcusdt_bot query-normal --client-order-id ENT-123
```

Точечно запросить статус algo order:

```bash
python -m btcusdt_bot query-algo --client-algo-id TP-123
```

Проверить countdown heartbeat в dry-run:

```bash
python -m btcusdt_bot heartbeat-watch --max-iterations 1
```

Запустить reconcile daemon на 3 итерации с targeted heal:

```bash
python -m btcusdt_bot reconcile-watch --interval-seconds 10 --max-iterations 3 --targeted-heal
```

Запустить breakout loop в dry-run режиме на 20 тиков:

```bash
python -m btcusdt_bot run-breakout-loop --max-messages 20 --lookback 60 --position-notional 100
```

Запустить breakout loop с trade-flow/funding/crowding gating, top-of-book guard, local depth gate, RPI-aware depth preference и adaptive sizing:

```bash
python -m btcusdt_bot run-breakout-loop \
  --max-messages 50 \
  --lookback 60 \
  --position-notional 100 \
  --min-recent-agg-trades 8 \
  --min-flow-imbalance 0.20 \
  --max-mark-trade-divergence-bps 2 \
  --max-positive-funding-rate 0.0003 \
  --max-crowding-snapshot-age-seconds 600 \
  --min-crowding-score 0.05 \
  --max-book-spread-bps 1.5 \
  --max-book-ticker-staleness-ms 1500 \
  --max-depth-snapshot-staleness-ms 1500 \
  --min-depth-imbalance 0.10 \
  --abstain-below-multiplier 0.60 \
  --volatility-target-atr-fraction 0.0020 \
  --max-expected-queue-clear-seconds 4 \
  --max-queue-ahead-to-order-ratio 8 \
  --min-exit-depth-coverage-ratio 0.75 \
  --max-exit-depth-sweep-bps 3 \
  --synthetic-tail-levels 3 \
  --synthetic-tail-replenishment-ratio 0.50 \
  --synthetic-tail-step-bps 1.0 \
  --with-depth-book \
  --with-rpi-depth-book \
  --use-rpi-depth-if-available
```


Прогнать parity backtest с RPI-aware depth fills:

```bash
python -m btcusdt_bot backtest-breakout \
  --strategy ensemble \
  --lookback 120 \
  --position-notional 100 \
  --use-local-depth-fills \
  --use-rpi-depth-fills
```

Отправлять demo/live ордера можно только явно, с работающим reconciliation и операторским бюджетом свежести:

```bash
python -m btcusdt_bot run-breakout-loop \
  --send \
  --with-private \
  --with-reconcile \
  --max-reconcile-staleness-ms "$MAX_RECONCILE_STALENESS_MS"
```

`MAX_RECONCILE_STALENESS_MS` не имеет значения по умолчанию: его нужно определить и проверить для конкретного runtime. Наличие флага не является разрешением live-торговли; все внешние guards и promotion gates остаются обязательными.

Прогнать parity backtest по накопленному `markPrice@1s` + `aggTrade` + `bookTicker` + `localDepth` + `!contractInfo` + crowding JSONL:

```bash
python -m btcusdt_bot backtest-breakout \
  --lookback 120 \
  --position-notional 100 \
  --max-crowding-snapshot-age-seconds 600 \
  --min-crowding-score 0.05 \
  --max-book-spread-bps 1.5 \
  --max-book-ticker-staleness-ms 1500 \
  --max-depth-snapshot-staleness-ms 1500 \
  --min-depth-imbalance 0.10 \
  --abstain-below-multiplier 0.60 \
  --volatility-target-atr-fraction 0.0020 \
  --max-expected-queue-clear-seconds 4 \
  --max-queue-ahead-to-order-ratio 8 \
  --min-exit-depth-coverage-ratio 0.75 \
  --max-exit-depth-sweep-bps 3 \
  --synthetic-tail-levels 3 \
  --synthetic-tail-replenishment-ratio 0.50 \
  --synthetic-tail-step-bps 1.0
```

Для parity с live economics-feedback можно включить backtest sizing по уже закрытым дням без look-ahead. На каждом тике backtest берет только dashboard, заканчивающийся предыдущим календарным днем:

```bash
python -m btcusdt_bot backtest-breakout \
  --lookback 120 \
  --position-notional 100 \
  --economics-lookback-days 7 \
  --economics-feedback-enabled \
  --economics-feedback-min-active-day-count 3 \
  --economics-feedback-min-multiplier 0.70 \
  --economics-regime-enabled \
  --economics-regime-min-active-day-count 3
```

Запустить walk-forward по rolling окнам и выбрать более устойчивый набор breakout-параметров без подглядывания в future fold:

```bash
python -m btcusdt_bot walkforward-breakout \
  --train-days 5 \
  --test-days 2 \
  --step-days 2 \
  --lookback-grid 60,120,180 \
  --hold-seconds-grid 180,300 \
  --min-flow-imbalance-grid 0,0.10,0.20 \
  --min-crowding-score-grid none,0.05 \
  --min-depth-imbalance-grid none,0.10 \
  --max-book-spread-bps-grid none,1.5 \
  --min-expected-fill-ratio-grid 0.35,0.50 \
  --max-drawdown-penalty 0.50 \
  --entry-timeout-rate-penalty 25 \
  --exit-depth-sweep-bps-penalty 2
```

При необходимости вернуться к legacy mark-only replay:

```bash
python -m btcusdt_bot backtest-breakout --mark-only --lookback 120 --position-notional 100
```

Проверить drift live-исполнения относительно последнего backtest baseline:

```bash
python -m btcusdt_bot execution-drift-watch --max-iterations 1
```

Проверить session PnL / drawdown guard по runtime-state:

```bash
python -m btcusdt_bot pnl-protection-watch --max-iterations 1
```

Проверить multi-day economics regime guard по authoritative session-truth reports:

```bash
python -m btcusdt_bot economics-regime-watch --lookback-days 7 --max-iterations 1
```

Проверить session-aware exchange-authoritative reconciliation по userTrades/income:

```bash
python -m btcusdt_bot trade-reconciliation-watch   --max-iterations 1   --session-state-path data/live/status/latest.json   --authoritative-archive-root data   --prefer-authoritative-archive   --hydrate-archive-gaps   --max-missing-local-order-ratio-observe 0.25   --max-quote-qty-abs-diff-usdt-observe 100   --max-income-trade-link-gap-ratio-observe 0.25
```

Построить authoritative session truth guard по session window и archive:

```bash
python -m btcusdt_bot session-truth-watch   --max-iterations 1   --session-state-path data/live/status/latest.json   --authoritative-archive-root data   --prefer-authoritative-archive   --hydrate-archive-gaps   --min-quote-qty-usdt 1000   --max-negative-net-realized-bps-observe 4   --min-maker-ratio-observe 0.20
```

Сделать long-window backfill userTrades/income в локальный authoritative archive:

```bash
python -m btcusdt_bot backfill-authoritative-history   --start-date 2026-04-01   --end-date 2026-04-07   --archive-root data   --income-window-days 7
```

Измерить post-fill markout по authoritative fills и одному явному `bookTicker` capture root:

```bash
python -m btcusdt_bot post-fill-markout \
  --start-date "$MARKOUT_START_DATE" \
  --end-date "$MARKOUT_END_DATE" \
  --archive-root data \
  --market-root "$MARKOUT_MARKET_ROOT" \
  --reference-source book_mid \
  --horizon-seconds "$MARKOUT_HORIZON_SECONDS" \
  --max-pre-fill-age-ms "$MARKOUT_MAX_PRE_FILL_AGE_MS" \
  --max-post-horizon-delay-ms "$MARKOUT_MAX_POST_DELAY_MS"
```

Запустить immutable forward-observer из корня TradingOS:

```powershell
$env:PYTHONPATH = "ops\btcusdt_binance_futures_bot\src"
$env:BOT_ENV = "demo"
$env:DATA_DIR = "data"
python -m btcusdt_bot post-fill-forward-observer `
  --prereg-path configs\POST_FILL_MARKOUT_FORWARD_PREREG_2026-07-14.json `
  --project-root .
```

Lock фиксирует общий cohort и горизонты `30s` (primary), `5s/300s` (diagnostic). Evidence floor `100 fills + 3 UTC-дня` разрешает только ручной обзор распределения. Команда не создаёт сигнал, entry, guard или ордер; без demo credentials честно возвращает `waiting_demo_credentials_for_authoritative_fills`.

`book_mid` даёт effective/realized spread и price impact. `mark_price` является только явно помеченным proxy. Horizon и freshness budgets обязательны, не имеют скрытых default и должны быть preregistered до открытия forward-выборки. Команда пишет research-only отчёт и не создаёт live guard.

Собрать unified combined guard с cooldown/hysteresis поверх execution/intraday/pnl/trade-reconciliation/session-truth слоёв:

```bash
python -m btcusdt_bot combined-protection-watch --max-iterations 1 --trade-reconciliation-guard-path data/live/guards/latest_trade_reconciliation.json --session-truth-guard-path data/live/guards/latest_session_truth.json
```

Запустить breakout loop с guard-файлами, которые могут переводить систему в reduce-size / observe-only:

```bash
python -m btcusdt_bot run-breakout-loop \
  --max-messages 50 \
  --execution-drift-guard-path data/live/guards/latest_execution_drift.json \
  --intraday-protection-guard-path data/live/guards/latest_intraday_protection.json \
  --pnl-protection-guard-path data/live/guards/latest_pnl_protection.json \
  --trade-reconciliation-guard-path data/live/guards/latest_trade_reconciliation.json \
  --session-truth-guard-path data/live/guards/latest_session_truth.json \
  --combined-protection-guard-path data/live/guards/latest_combined_protection.json
```

Собрать дневной агрегат по backtest/live/drift/pnl-репортам:

```bash
python -m btcusdt_bot aggregate-reports --date 2026-04-07
```

Собрать crowding snapshot в dry-run loop:

```bash
python -m btcusdt_bot collect-crowding --period 5m --max-iterations 1
```

Собрать payload normal order без отправки:

```bash
python -m btcusdt_bot submit-normal --side BUY --order-type LIMIT --qty 0.001 --price 65000 --mark-price 65010
```

Отправить test-order для normal path:

```bash
python -m btcusdt_bot submit-normal --side BUY --order-type LIMIT --qty 0.001 --price 65000 --mark-price 65010 --send --test
```

Собрать payload algo order без отправки:

```bash
python -m btcusdt_bot submit-algo --side SELL --order-type STOP_MARKET --qty 0.001 --trigger-price 64000 --reduce-only
```

## Что пишет collector

```text
data/
├── market/
│   └── YYYY-MM-DD/
│       ├── btcusdt_aggTrade.jsonl
│       ├── btcusdt_markPrice_1s.jsonl
│       ├── btcusdt_kline_1m.jsonl
│       ├── btcusdt_kline_5m.jsonl
│       └── contractInfo.jsonl
└── public/
    └── YYYY-MM-DD/
        ├── btcusdt_bookTicker.jsonl
        ├── btcusdt_depth_100ms.jsonl
        └── btcusdt_localDepth20.jsonl
```

## Что пишет private consumer

```text
data/
└── private/
    ├── YYYY-MM-DD/
    │   ├── ACCOUNT_UPDATE.jsonl
    │   ├── ALGO_UPDATE.jsonl
    │   ├── ORDER_TRADE_UPDATE.jsonl
    │   └── listenKeyExpired.jsonl
    └── state/
        └── latest.json
```

## Что пишет reconcile daemon и live loop

```text
data/
├── reconcile/
│   ├── latest_report.json
│   └── YYYY-MM-DD/
│       └── btcusdt_report.jsonl
└── live/
    ├── bootstrap_raw_snapshot.json
    ├── bootstrap_state.json
    ├── bootstrap_result.json
    ├── status/
    │   └── latest.json
    └── YYYY-MM-DD/
        └── btcusdt_actions.jsonl
```

## Что пишет bootstrap sync

```text
data/
└── bootstrap/
    ├── latest_raw.json
    └── state_after_sync.json
```

## Замечания по backtest

Текущий backtester детерминированный, но все еще упрощенный:

- parity-режим читает `markPrice@1s`, `aggTrade`, `bookTicker`, `localDepth` и `!contractInfo` JSONL в одном временном порядке;
- умеет flow/funding/contract-status/crowding gating и adaptive sizing, чтобы быть ближе к live loop;
- моделирует maker-first entry через top-of-book или local-depth resting limit logic;
- exit теперь оценивает taker execution через multi-level local-depth sweep с tail-penalty, если depth snapshot доступен;
- все еще не моделирует полный стакан, hidden liquidity, глубокую queue priority и deterministic multi-level fills beyond сохраненного top-N depth.

## Следующие шаги

1. Добавить более глубокий queue/depth replay beyond сохраненного top-N и отдельную модель hidden liquidity.
2. Вынести live execution-quality и daily diagnostics в отдельный CLI/report path, а не только в backtest JSON summary.
3. Расширить walk-forward на richer strategy interfaces, regime-specific candidate sets и полноценный walk-forward comparison report.
4. До live обязательно прогнать demo/testnet цикл и зафиксировать kill-criteria.


## Новое в v13

- `live/status/latest.json` по-прежнему пишет runtime status, но теперь рядом появляется `live/reports/latest_execution_quality.json` с агрегированными session-метриками.
- `reports/YYYY-MM-DD/btcusdt_live_execution_quality.jsonl` получает финальный session report после завершения loop.
- synthetic tail replay — это прокси за пределами видимого depth, а не обещание увидеть скрытую ликвидность биржи.

## v23 additions

The session-truth daemon now also writes a bucketed authoritative session report to:

- `data/live/reports/latest_session_truth_report.json`
- `data/reports/YYYY-MM-DD/btcusdt_session_truth_report.jsonl`

A new trend daemon can turn that report into a live guard:

```bash
python -m btcusdt_bot session-truth-watch --max-iterations 1
python -m btcusdt_bot session-truth-trend-watch --max-iterations 1
python -m btcusdt_bot combined-protection-watch --max-iterations 1 \
  --session-truth-trend-guard-path data/live/guards/latest_session_truth_trend.json
```

The live loop can also read this extra guard directly:

```bash
python -m btcusdt_bot run-breakout-loop \
  --session-truth-guard-path data/live/guards/latest_session_truth.json \
  --session-truth-trend-guard-path data/live/guards/latest_session_truth_trend.json \
  --combined-protection-guard-path data/live/guards/latest_combined_protection.json
```


## v29 regime router

The live loop, deterministic backtest and walk-forward optimizer now support three strategy modes through a shared strategy interface:

- `breakout` — continuation / breakout entries.
- `reversion` — range-mean-reversion entries gated by ATR extension and optional flow-flip confirmation.
- `router` — dynamic regime router that selects between breakout and reversion based on ATR fraction and flow regime, with optional opportunistic fallback.

Examples:

```bash
python -m btcusdt_bot run-breakout-loop \
  --strategy router \
  --lookback 120 \
  --reversion-entry-atr-multiple 1.25 \
  --router-range-max-atr-fraction 0.0060 \
  --router-trend-min-atr-fraction 0.0100

python -m btcusdt_bot backtest-breakout \
  --strategy router \
  --lookback 120 \
  --reversion-entry-atr-multiple 1.25 \
  --router-range-max-atr-fraction 0.0060 \
  --router-trend-min-atr-fraction 0.0100

python -m btcusdt_bot walkforward-breakout \
  --strategy breakout \
  --strategy-grid breakout,reversion,router \
  --lookback-grid 60,120,180 \
  --reversion-entry-atr-multiple-grid 1.0,1.25,1.5 \
  --reversion-max-atr-fraction-grid 0.0030,0.0040,0.0050 \
  --reversion-min-flow-flip-grid 0,0.05
```

## v30 online ensemble selector

A new `ensemble` strategy mode now sits above the shared breakout/reversion interface.

What changed:
- `ensemble` evaluates `breakout` and `reversion` on the same tick and keeps the same execution/risk path as the rest of the system.
- Selection is no longer only rule-based: the ensemble blends a regime prior (`range` prefers reversion, `trend` prefers breakout) with online strategy health.
- Online strategy health is updated from realized entry outcomes in live/backtest (`fill_ratio_shortfall`, `latency_overshoot`, `timeout_rate`) and from realized trade PnL in backtest.
- Walk-forward can now compare `breakout`, `reversion`, `router` and `ensemble` in one family search.

New quick starts:

```bash
python -m btcusdt_bot run-breakout-loop \
  --strategy ensemble \
  --lookback 120 \
  --reversion-entry-atr-multiple 1.25 \
  --router-range-max-atr-fraction 0.0060 \
  --router-trend-min-atr-fraction 0.0100

python -m btcusdt_bot backtest-breakout \
  --strategy ensemble \
  --lookback 120 \
  --reversion-entry-atr-multiple 1.25 \
  --router-range-max-atr-fraction 0.0060 \
  --router-trend-min-atr-fraction 0.0100

python -m btcusdt_bot walkforward-breakout \
  --strategy breakout \
  --strategy-grid breakout,reversion,router,ensemble \
  --lookback-grid 60,120,180 \
  --reversion-entry-atr-multiple-grid 1.0,1.25,1.5
```
