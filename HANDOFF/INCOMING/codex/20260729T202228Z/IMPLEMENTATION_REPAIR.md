# R62 evaluator implementation repair

The first corrected-source evaluator invocation failed before threshold
calculation and before any result was emitted.

Observed defects:

1. The evaluator still searched the superseded monthly metrics path fragment,
   producing an empty metrics input.
2. Public daily metrics contain occasional blank records; the parser did not
   treat them as missing observations.

Repair:

- Point the evaluator at the frozen official daily metrics path.
- Skip rows whose OI or top-position-ratio field is blank.
- Retain the existing 10-minute causal freshness limit, so missing records do
  not become unbounded forward fills.

No analytical feature, quantile, threshold, period, cost, horizon, matching
rule, bootstrap, sample floor, or disposition gate changed.

No threshold or outcome result existed before this repair.

`can_trade=false`

`capital_permission=DENY`
