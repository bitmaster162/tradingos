# R100 execution realism shadow

Status: isolated research candidate stacked on R99. No runtime deployment.

## Findings

The shared OHLC simulator fills a stop at the configured stop price whenever a
bar high/low crosses that stop. If a later bar opens beyond the stop, that is
optimistic for a stop-market style execution: the old replay still exits at the
better stop level instead of the worse opening price.

The Phase-A cache used for the current relative-strength research contains OHLCV
bars but no funding-rate, premium-index, mark-price or basis time series. The
existing cost model therefore includes fee/slippage bps only and cannot honestly
claim funding-adjusted perpetual-futures returns.

## Shadow model

This candidate is intentionally downgrade-only and post-hoc:

- adverse stop gaps exit at the worse bar open;
- favorable take gaps are capped at the configured target;
- same-bar stop/take ambiguity remains stop-first;
- standard 00/08/16 UTC funding boundaries crossed are counted only as exposure
  diagnostics;
- funding PnL remains UNMODELED_NO_HISTORICAL_RATE_SERIES;
- no positive shadow result can promote or rehabilitate a strategy because the
  old OOS has already been inspected.

The standard 8h boundary counter is diagnostic only. Binance funding frequency
can change by contract/time, so actual historical funding requires the actual
rate/settlement series.

research_only=true; can_trade=false; capital_permission=DENY.
