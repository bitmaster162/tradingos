# Trading OS Control Panel

Local web control panel for the unified package.

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_control_panel.ps1
```

Open:

```text
http://127.0.0.1:8765
```

Safety model:

- binds to `127.0.0.1` by default;
- exposes only hard-coded allowlisted commands;
- scrubs Binance and wallet private-key environment variables before child commands;
- does not expose `--send`, private streams, approvals, wallet signing, or arbitrary shell input;
- treats the historical full MAX Pipeline core as missing;
- exposes the repo-local `MAX_CORE_LITE` replacement only as smoke/research runtime.
- shows the isolated research runtime supervisor as a read-only health card; the panel cannot restart its processes.

Allowed actions:

- bounded smoke-pack;
- update MANIFEST safely;
- Telegram config audit;
- futures local tests;
- futures market manifest;
- futures plan example;
- futures deterministic backtest smoke;
- futures public websocket single-message proof;
- DEX paper range smoke;
- Delist EWS compile check;
- MAX Core Lite composite report;
- MAX Core Lite BTC 1h research backtest;
- MAX Core Lite v0.5 walk-forward discovery leaderboard;
- MAX Core Lite v0.6 labelled event export;
- MAX Core Lite v0.7 feature-slice miner;
- MAX Core Lite v0.8 mined-strategy diagnostic;
- MAX Core Lite v0.9 multi-timeframe research grid;
- MAX Core Lite public-data cache update;
- MAX Core Lite v1.0 candidate hardening pack using local cache when available;
- MAX Core Lite v1.1 weak-bid candidate validation with folds and bootstrap;
- MAX Core Lite v1.2 regime isolation over v1.1 executed trades;
- MAX Core Lite v1.3 structural candidate validation from raw market data;
- MAX Core Lite v1.4 larger-sample LONG/SHORT expansion;
- MAX Core Lite v1.5 OI/funding + sweep/liquidity + HTF state filters;
- MAX Core Lite v1.6 event-first miner for OI/funding + sweep/liquidity + HTF;
- MAX Core Lite v1.7 targeted short-continuation hardening;
- MAX Core Lite v1.8 alert-only `short_continuation_pressure` inside the composite report;
- MAX Core Lite v1.9 alert observability JSONL log and forward tracker;
- MAX Core Lite v2.0 forward-evidence scoreboard;
- edge registry;
- edge forward candidate export;
- edge forward RANGE observer;
- edge forward pending watch;
- edge forward RANGE scoreboard;
- edge forward pending-watch Telegram notify;
- edge forward promotion gate;
- forward outcome accumulator;
- forward entry scarcity diagnostic;
- forward shadow relaxation validator;
- range family validator;
- range watchlist refiner;
- range refined forward observer;
- range refined observer scoreboard;
- range refined signal scarcity diagnostic;
- range refined pending watch;
- range pending-watch Telegram notify;
- range pending-watch Telegram drill;
- range refined filter shadow ablation;
- range refined filter shadow forward observer;
- range refined filter shadow forward scoreboard;
- range refined filter shadow promotion gate;
- range refined promotion gate;
- range refined signal alert guard;
- range refined signal alert drill.
