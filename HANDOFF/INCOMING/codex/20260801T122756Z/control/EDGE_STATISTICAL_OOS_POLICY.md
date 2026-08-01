# Edge Research Statistical and OOS Policy R1

## Final-test integrity

The final test period is frozen before outcome calculation. No threshold,
feature, regime, horizon, cost assumption, or exclusion rule may be chosen by
examining final-test results.

## Overlapping windows

When event windows overlap, use an explicit purge/embargo or event-cluster rule.
Report both raw event count and effective independent event count.

## Multiple testing

All hypotheses and primary variants in a cycle form one declared testing family.
Use Holm correction or a more conservative procedure. Exploratory variants are
reported separately and cannot support `KEEP_FOR_FORWARD_PAPER`.

## Costs

Primary results are net of fees, spread, slippage, funding where applicable, and
a stated latency model. At least one adverse-cost scenario is mandatory.

## Robustness

Required where applicable:

- time-ordered walk-forward;
- delayed entry;
- alternate event dedupe;
- regime stratification;
- one-source-removed ablation;
- placebo or permutation;
- direction randomization;
- outlier/tail sensitivity;
- source and clock-skew diagnostics.

## Rare events

Do not invent a universal event-count threshold. The preregistration must contain
a hypothesis-specific power analysis or a sequential evidence plan. If evidence
cannot support a stable conclusion, use `INSUFFICIENT_DATA`.

## Interpretation

- Statistical significance alone is insufficient.
- Positive mean with unacceptable tail risk is insufficient.
- One regime, one source, or one event dominating the result is insufficient.
- `KEEP_FOR_FORWARD_PAPER` is a measurement authorization, not a trading signal.
