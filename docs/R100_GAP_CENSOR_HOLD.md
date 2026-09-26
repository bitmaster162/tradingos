# R100 gap-aware and right-censored OHLC replay

Status: isolated candidate stacked on R99. No runtime deployment.

Base: 11e78012786b5d18816f4c7cde17ef734d18c53c

## Findings

The shared TradingOS OHLC simulator had three material semantics problems:

1. On a later candle opening beyond an adverse stop, it filled at the stop price instead of
   the worse observed open. This can understate losses.
2. If the data/window ended before max_hold_bars elapsed and no stop/take was hit, it forced
   a shortened time exit at the last available close instead of treating the trade as censored.
3. max_hold_bars was implemented as entry_index + max_hold_bars inclusive, producing up to
   max_hold_bars + 1 held bars.

## Candidate semantics

- Adverse stop gaps fill at the observed bar open.
- Favorable take gaps receive no improvement beyond the take level.
- Same-bar unknown ordering remains conservative stop-first.
- max_hold_bars is exactly the inclusive count from entry through the time-exit bar.
- If the full time-exit horizon is unavailable, unresolved trades return None/censored.
- A stop/take already observed before the truncation remains a valid resolved replay.
- Unknown sides and invalid hold budgets fail closed.

This changes historical replay semantics. Prior reports must not be silently relabeled or
rewritten. Any comparison requires a new versioned rerun.

This remains OHLC replay, not tick-accurate or observed execution. Funding, queue position,
spread dynamics and portfolio sizing are still outside this model.

research_only=true; can_trade=false; capital_permission=DENY.
