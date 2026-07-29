# TradingOS R59 bounded edge research cycle 03

Terminal: `TWO_HYPOTHESES_DISPOSED`

This was a research and falsification cycle only. It did not modify TradingOS
implementation, runtime state, permissions, Scheduler state, signals, orders, or
capital.

## Causal spine

1. The accepted R57 strict ZIP and proposal bundle were verified.
2. The unchanged R57 strict triplet was published through Return Broker before
   R59 execution.
3. A disposable branch was created from accepted R57 HEAD
   `47349d6949a8d552c90f53a0e34b4fa8c540d87e`.
4. Exactly H01 and H02 were selected from the unresolved R57 watchlist.
5. Parameters, sources, costs, causal entry/exit, invalidation, sample floors,
   and prohibited adaptations were committed at
   `f95bde5e9b6a36846d864ce07bc9d7dda1ff4a4f`.
6. Only after that commit, 24 frozen public Binance Vision archives for
   January through June 2026 were retrieved and SHA-256 hashed.
7. Two accepted R57 OOS ledgers were copied unchanged and hash-verified.
8. The frozen evaluator ran twice. All 11 generated files were identical by
   SHA-256 across runs.
9. Ten offline contract tests passed.
10. Active integrity remained clean: 1,078 files checked, zero drift,
    `can_trade=false`.

## H01 BTC funding-extreme reversal

Disposition: `INSUFFICIENT_DATA`

- Prior 2025 OOS trades: 0
- New 2026-H1 trades: 0
- Cumulative OOS trades: 0
- Requirement to classify: 20 additional frozen OOS trades
- Requirement to reach keep sample floor: 30 additional trades

The public source contained 543 BTCUSDT funding observations, but none crossed
the frozen absolute threshold of 0.0003 while satisfying the causal trade
contract. The mechanism is not proven and not falsified. No threshold relaxation
is allowed in this cycle.

## H02 BTC to ETH lead-lag

Disposition: `INSUFFICIENT_DATA`

- Prior 2025 OOS trades: 7
- New 2026-H1 trades: 5
- Cumulative OOS trades: 12
- Cumulative net mean: 0.0035292261037883493
- Cumulative net median: -0.00467574860040397
- Cumulative win rate: 0.3333333333333333
- Cumulative bootstrap 95 percent lower mean: -0.011814738553037404
- Positive-return quarter concentration: 0.7851743609247797
- Requirement to classify: 28 additional frozen OOS trades
- Requirement to reach keep sample floor: 88 additional trades

The positive mean is not sufficient evidence. The median is negative, the
bootstrap lower bound is negative, gains are concentrated, and the sample is
below the frozen classification floor.

## Strict interpretation

Neither hypothesis is a trading edge at this stage. Neither may produce a live,
testnet, or paper signal from this result. The only allowed continuation is a
future append-only observation window with the same frozen contract or an
explicitly new hypothesis in a later preregistered cycle.

`can_trade=false`

`capital_permission=DENY`
